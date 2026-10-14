import copy
import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from tractor_sim.checkpoint import dump_registry, load_registry, private_json
from tractor_sim.engine import GameConfig
from tractor_sim.live import Gateway, Worker, stage_release
from tractor_sim.policy import RandomPolicy
from tractor_sim.server import Registry, Room, TractorServer
from tools.update_live import submit

ROOT = Path(__file__).resolve().parent.parent


class Checkpoints(unittest.TestCase):
    def setUp(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'build')
        self.addCleanup(self.tmp.cleanup)
        self.registry = Registry(history_dir=Path(self.tmp.name) / 'history')

    def new_room(self, code='live', mode='pvp'):
        room = Room(code, mode, GameConfig(), seed=4, history=self.registry.history)
        self.registry.rooms[code] = room
        return room

    def roundtrip(self):
        document = json.loads(json.dumps(self.registry.freeze()))
        restored = Registry(history_dir=Path(self.tmp.name) / 'history')
        load_registry(restored, document)
        return restored

    def test_all_seats_credentials_rng_and_room_controls_survive(self):
        room = self.new_room()
        tokens = []
        for seat in range(4):
            token = room.join('人' + str(seat))
            with room.condition:
                room.confirm_seat(token, seat)
            tokens.append(token)
            self.registry.tokens[token] = room
        room.clients[tokens[1]]['takeover'] = True
        room.departed_names[3] = '旧玩家'
        room.autoplay = True
        room.policy_epochs = [3, 1, 7, 2]
        room.env.passed = [{2, 5}, {2}]
        room.diagnostic_keys.add((0, (1, 55)))
        restored = self.roundtrip()
        other = restored.lookup(tokens[0])
        self.assertEqual(other.env.export_state(), room.env.export_state())
        self.assertEqual(other.host, tokens[0])
        self.assertEqual([restored.lookup(t).clients[t]['seat'] for t in tokens], list(range(4)))
        self.assertTrue(other.clients[tokens[1]]['takeover'])
        self.assertEqual(other.departed_names, room.departed_names)
        self.assertEqual(other.policy_epochs, room.policy_epochs)
        self.assertEqual(other.diagnostic_keys, room.diagnostic_keys)
        for original, cloned in zip(room.policies, other.policies):
            self.assertEqual(original.rng.random(), cloned.rng.random())
        self.assertEqual(room.policy.rng.random(), other.policy.rng.random())
        self.assertEqual(room.env.rng.random(), other.env.rng.random())

    def test_ai_review_acknowledgments_and_record_setting_survive_worker_replacement(self):
        from test_table_review import TableReviewTests
        room=Room('reviewlive','pvp',GameConfig(auction=False,banker=2),18,history=self.registry.history)
        self.registry.rooms[room.code]=room
        tokens=[room.join('玩家'+str(i)) for i in range(2)]
        with room.condition:
            for seat,token in enumerate(tokens):
                room.confirm_seat(token,seat);self.registry.tokens[token]=room
            room.set_ai(tokens[0],None,True);room.start_room(tokens[0])
            TableReviewTests.finish(self,room)
            room.set_record_plays(tokens[0],True);room.confirm_result(tokens[0],1)
        restored=self.roundtrip();other=restored.lookup(tokens[1]);restored.resume()
        with other.condition:
            review=other.snapshot(tokens[1])['settlement']
            self.assertEqual([p['seat'] for p in review['pending_humans']],[1])
            self.assertTrue(other.snapshot(tokens[0])['settlement']['own_confirmed'])
            other.confirm_result(tokens[1],1)
            self.assertEqual(other.env.round_no,2)
            self.assertTrue(other.record_plays)

    def test_bidding_timer_disconnect_clock_and_replay_exclude_update_pause(self):
        room = self.new_room(mode='self')
        token = room.join('test', 0)
        self.registry.tokens[token] = room
        while room.env.deal_count < 100:
            with room.condition:
                room.step({'type': 'pass'})
        self.assertIsNotNone(room.bidding_deadline)
        room.grace_policy_seen.add((room.env.revision, 2))
        before = time.monotonic()
        remaining = room.bidding_deadline - before
        age = before - room.clients[token]['seen']
        replay_elapsed = before - room.recording.clock
        document = self.registry.freeze()
        # Simulate a long deployment without sleeping 30 seconds.
        document['clock'] -= 30
        for field in ('updated', 'next_bot_at', 'bidding_deadline'):
            document['rooms'][0][field] -= 30
        document['rooms'][0]['clients'][token]['seen'] -= 30
        document['rooms'][0]['recording']['clock'] -= 30
        restored = Registry(history_dir=Path(self.tmp.name) / 'history')
        load_registry(restored, json.loads(json.dumps(document)))
        other = restored.lookup(token)
        restored.frozen_at -= 10
        for field in ('updated', 'next_bot_at', 'bidding_deadline'):
            setattr(other, field, getattr(other, field) - 10)
        other.clients[token]['seen'] -= 10
        other.recording.clock -= 10
        restored.resume()
        now = time.monotonic()
        self.assertAlmostEqual(other.bidding_deadline - now, remaining, delta=.1)
        self.assertAlmostEqual(now - other.clients[token]['seen'], age, delta=.1)
        self.assertAlmostEqual(now - other.recording.clock, replay_elapsed, delta=.1)
        self.assertEqual(other.bidding_window_key[0], other.env)
        self.assertEqual(other.grace_policy_seen, room.grace_policy_seen)
        self.assertEqual(other.recording.frames, room.recording.frames)

    def test_pending_ai_is_drained_not_applied_and_rng_preserved(self):
        room = self.new_room()
        revision = room.env.revision
        def computation():
            time.sleep(.08)
            room.policies[0].rng.random()
            return {'type': 'pass'}
        with ThreadPoolExecutor(1) as pool:
            room.pending = (pool.submit(computation), revision, 0, 0)
            restored = self.roundtrip()
        self.assertIsNone(room.pending)
        self.assertIsNone(restored.rooms[room.code].pending)
        self.assertEqual(room.env.revision, revision)
        self.assertEqual(room.policies[0].rng.random(), restored.rooms[room.code].policies[0].rng.random())

    def test_active_replay_continues_and_saves_one_complete_round(self):
        room = self.new_room(mode='self')
        token = room.join('test', 0)
        self.registry.tokens[token] = room
        with room.condition:
            room.step({'type': 'pass'})
        other_registry = self.roundtrip()
        other_registry.resume()
        other = other_registry.lookup(token)
        first_frames = copy.deepcopy(other.recording.frames)
        policy = RandomPolicy(7)
        for _ in range(2000):
            if other.env.phase in ('round_end', 'match_end'):
                break
            with other.condition:
                if other.bidding_deadline is not None:
                    other.close_bidding_if_due(other.bidding_deadline + .01)
                else:
                    other.step(policy.act(other.env.observe(other.env.current_player)))
        self.assertEqual(other.env.phase, 'round_end')
        self.assertEqual(other.recording.frames[:len(first_frames)], first_frames)
        self.assertIsNotNone(other.replay_id)
        self.assertEqual(len(list(other.history.directory.glob('*.json'))), 1)
        self.assertEqual(other.history.read(other.replay_id)['frames'][-1]['state']['phase'], 'round_end')

    def test_incompatible_checkpoint_leaves_registry_untouched(self):
        room = self.new_room()
        with self.assertRaises(ValueError):
            load_registry(self.registry, {'schema': 99})
        self.assertIs(self.registry.rooms['live'], room)

    def test_restore_during_burial_countering_partial_trick_and_settlement(self):
        room = self.new_room(mode='self')
        token = room.join('test', 0)
        self.registry.tokens[token] = room
        policy = RandomPolicy(42)
        seen = set()
        for _ in range(1500):
            phase = room.env.phase
            key = 'partial_trick' if phase == 'playing' and room.env.trick else phase
            if key not in seen:
                seen.add(key)
                document = json.loads(json.dumps(self.registry.freeze()))
                restored = Registry(history_dir=Path(self.tmp.name) / 'history')
                load_registry(restored, document)
                cloned = restored.lookup(token)
                self.assertEqual(cloned.env.export_state(), room.env.export_state())
                self.assertEqual(cloned.recording.frames, room.recording.frames)
                self.assertEqual(cloned.review_confirmed_by, room.review_confirmed_by)
                restored.resume(); self.registry.resume()
            if phase == 'round_end':
                break
            action = policy.act(room.env.observe(room.env.current_player))
            # Keep a single declaration to exercise three-seat counter polling.
            if phase in ('dealing', 'final_bidding', 'countering'):
                options = room.env.bid_options(room.env.current_player)
                singles = [a for a in options if len(a['cards']) == 1]
                action = singles[0] if room.env.bid is None and singles else {'type': 'pass'}
            with room.condition:
                if room.bidding_deadline is not None:
                    room.close_bidding_if_due(room.bidding_deadline + .01)
                else:
                    room.step(action)
        self.assertTrue({'dealing', 'final_bidding', 'burying', 'countering', 'playing',
                         'partial_trick', 'round_end'} <= seen, seen)


class LiveHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'build')
        cls.base = Path(cls.tmp.name)
        cls.source = cls.base / 'source'
        for folder in ('tractor_sim', 'web', 'rules'):
            shutil.copytree(ROOT / folder, cls.source / folder, ignore=shutil.ignore_patterns('__pycache__'))
        cls.gateway = Gateway(('127.0.0.1', 0), source=cls.source, operations=cls.base / 'operations')
        cls.thread = threading.Thread(target=cls.gateway.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.gateway.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.gateway.shutdown()
        cls.gateway.server_close()
        cls.thread.join(2)
        cls.tmp.cleanup()

    def request(self, path, data=None, token=None):
        request = urllib.request.Request(self.url + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {})})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def change_frontend(self, marker):
        path = self.source / 'web' / 'index.html'
        path.write_text(path.read_text(encoding='utf-8') + '<!-- ' + marker + ' -->', encoding='utf-8')
        return stage_release(self.source, self.gateway.operations)

    def test_four_players_stay_seated_and_same_hand_after_frontend_backend_swap(self):
        clients = [self.request('/api/session', {'mode': 'pvp', 'room': 'rolling', 'name': str(i), 'seed': 6})[1]
                   for i in range(4)]
        for i, client in enumerate(clients):
            self.assertEqual(self.request('/api/seat/confirm', {'seat': i}, client['token'])[0], 200)
        self.request('/api/room/start', {}, clients[0]['token'])
        for _ in range(8):
            states = [self.request('/api/state', token=c['token'])[1] for c in clients]
            actor = states[0]['state']['current_player']
            self.request('/api/action', {'version': states[actor]['version'], 'action': {'type': 'pass'}}, clients[actor]['token'])
        before = [self.request('/api/state', token=c['token'])[1] for c in clients]
        old_pid = self.gateway.worker.process.pid
        health = self.request('/api/health')[1]
        public_url = 'https://steady.trycloudflare.com'
        private_json(self.gateway.public_entry, {'instance_id': health['instance_id'], 'public_url': public_url})
        self.assertEqual(self.request('/api/site')[1]['public_url'], public_url)
        release = self.change_frontend('new-frontend')
        result = submit(self.source, self.gateway.operations, self.gateway.server_port)
        self.assertTrue(result['changed'])
        self.assertNotEqual(old_pid, self.gateway.worker.process.pid)
        self.assertEqual(self.request('/api/health')[1]['instance_id'], health['instance_id'])
        self.assertEqual(self.request('/api/site')[1]['public_url'], public_url)
        self.assertIn(b'new-frontend', self.request('/')[1])
        for c, prior in zip(clients, before):
            status, state = self.request('/api/state', token=c['token'])
            self.assertEqual(status, 200)
            self.assertEqual(state['seat'], prior['seat'])
            self.assertEqual(state['version'], prior['version'])
            self.assertEqual(state['state']['hand'], prior['state']['hand'])
            self.assertNotIn('hands', state['state'])
        actor = before[0]['state']['current_player']
        self.assertEqual(self.request('/api/action', {'version': before[actor]['version'],
            'action': {'type': 'pass'}}, clients[actor]['token'])[0], 200)
        self.assertEqual(self.gateway.worker.release, release)

    def test_requests_queue_while_updating_without_new_session(self):
        state = self.request('/api/session', {'name': '测试玩家', 'mode': 'self', 'seed': 9})[1]
        release = self.change_frontend('queue-check')
        entered = threading.Event()
        original = Worker.__init__
        def slow_start(worker, *args, **kwargs):
            entered.set()
            time.sleep(.4)
            original(worker, *args, **kwargs)
        with patch.object(Worker, '__init__', slow_start), ThreadPoolExecutor(3) as pool:
            update = pool.submit(self.gateway.update, release)
            self.assertTrue(entered.wait(5))
            self.assertTrue(self.request('/api/health')[1]['updating'])
            response = pool.submit(self.request, '/api/state', None, state['token'])
            action = pool.submit(self.request, '/api/action', {'version': state['version'],
                'action': {'type': 'pass'}}, state['token'])
            self.assertFalse(response.done())
            self.assertTrue(update.result(20)['ok'])
            status, resumed = response.result(10)
            self.assertEqual(action.result(10)[0], 200)
        self.assertEqual(status, 200)
        self.assertEqual(resumed['room'], state['room'])
        # GET and POST released from the queue may arrive in either order.
        # Self mode's `hand` follows current_player, unlike PVP's fixed seat.
        self.assertIn(resumed['state']['deal_count'], (state['state']['deal_count'], state['state']['deal_count'] + 1))
        latest = self.request('/api/state', token=state['token'])[1]
        self.assertEqual(latest['state']['deal_count'], state['state']['deal_count'] + 1)
        self.assertEqual(latest['state']['hands'][0], state['state']['hands'][0])

    def test_long_poll_is_drained_before_switching_workers(self):
        state = self.request('/api/session', {'name': '测试玩家', 'mode': 'self', 'seed': 21})[1]
        release = self.change_frontend('long-poll')
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self.request, f"/api/state?since={state['version']}&wait=0.5", None, state['token'])
            deadline = time.monotonic() + 2
            while self.gateway.active == 0 and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.gateway.active, 1)
            self.assertTrue(self.gateway.update(release)['ok'])
            status, poll = pending.result(5)
        self.assertEqual(status, 200)
        self.assertEqual(poll['version'], state['version'])
        self.assertEqual(self.request('/api/state', token=state['token'])[0], 200)

    def test_legacy_service_refuses_update_without_restart(self):
        server = TractorServer(('127.0.0.1', 0), history_dir=self.base / 'legacy-history')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaisesRegex(RuntimeError, '未停止或重启'):
                submit(self.source, self.gateway.operations, server.server_port)
            self.assertTrue(thread.is_alive())
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def test_bad_backend_rolls_back_keeps_old_html_and_tokens(self):
        state = self.request('/api/session', {'name': '测试玩家', 'mode': 'self', 'seed': 11})[1]
        old, html = self.gateway.worker, self.request('/')[1]
        path = self.source / 'tractor_sim' / 'worker.py'
        original = path.read_text(encoding='utf-8')
        try:
            path.write_text('raise RuntimeError("failed deployment")\n' + original, encoding='utf-8')
            release = self.change_frontend('broken-do-not-promote')
            with self.assertRaises(RuntimeError):
                self.gateway.update(release)
        finally:
            path.write_text(original, encoding='utf-8')
        self.assertIs(self.gateway.worker, old)
        self.assertIsNone(old.process.poll())
        self.assertEqual(self.request('/')[1], html)
        self.assertEqual(self.request('/api/state', token=state['token'])[0], 200)
        metadata = json.loads((self.gateway.operations / 'gateway.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['release'], old.release.name)
        self.assertFalse(list(self.gateway.operations.glob('checkpoint-*.json')))

    def test_private_admin_is_not_forwarded_even_with_control_key(self):
        for path in ('/api/internal/freeze', '/api/%69nternal/freeze'):
            self.assertEqual(self.request(path, {})[0], 404)
        conn = __import__('http.client', fromlist=['HTTPConnection']).HTTPConnection('127.0.0.1', self.gateway.worker.port)
        conn.request('POST', '/api/internal/freeze', body=b'', headers={'Content-Length': '0'})
        self.assertEqual(conn.getresponse().status, 404)
        conn.close()

    def test_same_release_is_noop_and_runtime_not_packaged(self):
        release = self.gateway.worker.release
        self.assertFalse(self.gateway.update(release)['changed'])
        self.assertFalse((release / 'runtime').exists())
        self.assertFalse((release / '.git').exists())

    def test_reset_ends_rooms_but_keeps_gateway_public_domain_and_instance(self):
        state = self.request('/api/session', {'name': '测试玩家', 'mode': 'self'})[1]
        instance = self.gateway.instance_id
        public_url = 'https://same-domain.trycloudflare.com'
        private_json(self.gateway.public_entry, {'instance_id': instance, 'public_url': public_url})
        result = submit(self.source, self.gateway.operations, self.gateway.server_port, reset_rooms=True)
        self.assertTrue(result['rooms_reset'])
        self.assertEqual(self.gateway.instance_id, instance)
        self.assertEqual(self.request('/api/site')[1]['public_url'], public_url)
        status, stale = self.request('/api/state', token=state['token'])
        self.assertEqual(status, 403)
        self.assertEqual(stale['code'], 'SESSION')
        self.assertEqual(self.request('/api/session', {'name': '测试玩家', 'mode': 'self'})[0], 200)
