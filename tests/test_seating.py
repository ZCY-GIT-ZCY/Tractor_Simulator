import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tractor_sim.engine import GameConfig
from tractor_sim.enoch import EnochPolicy
from tractor_sim.policy import RandomPolicy
from tractor_sim.server import Room, TractorServer, AI_REVIEW_SECONDS

ROOT = Path(__file__).resolve().parent.parent


class SeatingAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'build')
        cls.server = TractorServer(('127.0.0.1', 0), policy_factory=RandomPolicy,
                                   history_dir=Path(cls.tmp.name) / 'history')
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.worker.join(2)
        cls.tmp.cleanup()

    def request(self, endpoint, body=None, token=None):
        req = urllib.request.Request(self.url + endpoint,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r: return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def enter(self, name='Host', room=None, banker=3):
        status, session = self.request('/api/session', {'mode': 'pvp', 'name': name,
            'room': room or 'seat_' + hashlib.sha256(self._testMethodName.encode()).hexdigest()[:12],
            'seed': 18, 'config': {'auction': False, 'banker': banker}})
        self.assertEqual(status, 200)
        return session

    def confirm(self, client, seat):
        status, state = self.request('/api/seat/confirm', {'seat': seat}, client['token'])
        self.assertEqual(status, 200, state)
        return state

    def test_join_does_not_claim_then_cancel_releases_to_another_player(self):
        host, friend = self.enter(), self.enter('Friend')
        self.assertIsNone(host['seat']); self.assertIsNone(host['state'])
        self.assertFalse(any(p['occupied'] for p in host['players']))
        confirmed = self.confirm(host, 3)
        self.assertEqual(confirmed['players'][3]['name'], 'Host')
        self.assertFalse(confirmed['started'])
        version = confirmed['version']
        self.assertEqual(self.confirm(host, 3)['version'], version)
        status, error = self.request('/api/seat/confirm', {'seat': 1}, host['token'])
        self.assertEqual(error['code'], 'SEAT_CONFIRMED')
        status, state = self.request('/api/seat/cancel', {}, host['token'])
        self.assertEqual(status, 200); self.assertIsNone(state['seat'])
        self.assertFalse(state['players'][3]['occupied'])
        self.assertEqual(self.confirm(friend, 3)['players'][3]['name'], 'Friend')

    def test_simultaneous_confirmation_has_exactly_one_winner(self):
        a, b = self.enter('A'), self.enter('B')
        barrier = threading.Barrier(2)
        def claim(c):
            barrier.wait()
            return self.request('/api/seat/confirm', {'seat': 2}, c['token'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, (a, b)))
        self.assertEqual(sorted(status for status, _ in results), [200, 422])
        self.assertEqual(next(r['code'] for status, r in results if status == 422), 'SEAT_TAKEN')
        room = self.server.registry.lookup(a['token'])
        self.assertEqual(sum(c['seat'] == 2 for c in room.clients.values()), 1)

    def test_bad_seats_do_not_mutate_and_different_seats_do_not_need_room_revision(self):
        host, friend = self.enter(), self.enter('Friend')
        room = self.server.registry.lookup(host['token'])
        before = copy.deepcopy(room.env.export_state())
        for seat in (None, True, -1, 4, '2', [], {}):
            status, _ = self.request('/api/seat/confirm', {'seat': seat}, host['token'])
            self.assertEqual(status, 400)
            self.assertIsNone(room.clients[host['token']]['seat'])
        self.confirm(host, 2); self.confirm(friend, 0)
        self.assertEqual(before, room.env.export_state())

    def test_host_can_fill_or_remove_ai_but_cannot_overwrite_human_or_start_early(self):
        host, friend = self.enter(), self.enter('Friend')
        self.confirm(host, 3); self.confirm(friend, 1)
        status, error = self.request('/api/room/start', {}, host['token'])
        self.assertEqual(error['code'], 'SEATS_NOT_READY')
        status, error = self.request('/api/room/ai', {'seat': 0, 'enabled': True}, friend['token'])
        self.assertEqual(error['code'], 'HOST_ONLY')
        status, error = self.request('/api/room/ai', {'seat': 1, 'enabled': True}, host['token'])
        self.assertEqual(error['code'], 'SEAT_TAKEN')
        status, state = self.request('/api/room/ai', {'enabled': True}, host['token'])
        self.assertEqual(status, 200); self.assertEqual(state['ai_seats'], [0, 2])
        self.assertTrue(state['can_start']); self.assertFalse(state['started'])
        status, state = self.request('/api/room/ai', {'seat': 0, 'enabled': False}, host['token'])
        self.assertFalse(state['players'][0]['occupied'])
        self.confirm(self.enter('New'), 0)
        status, error = self.request('/api/room/start', {}, friend['token'])
        self.assertEqual(error['code'], 'HOST_ONLY')
        status, state = self.request('/api/room/start', {}, host['token'])
        self.assertEqual(status, 200); self.assertTrue(state['started'])
        self.assertNotIn('hands', state['state'])
        self.assertEqual(sum(p['bot'] for p in state['players']), 1)
        status, error = self.request('/api/seat/cancel', {}, host['token'])
        self.assertEqual(error['code'], 'SEATS_LOCKED')

    def test_unseated_waiter_neither_receives_cards_nor_pauses_game(self):
        host, waiter = self.enter(), self.enter('Waiter')
        self.confirm(host, 3)
        self.request('/api/room/ai', {'enabled': True}, host['token'])
        self.request('/api/room/start', {}, host['token'])
        room = self.server.registry.lookup(host['token'])
        with room.condition:
            room.clients[waiter['token']]['seen'] -= 40
        _, state = self.request('/api/state', token=host['token'])
        self.assertFalse(state['paused'])
        _, waiting = self.request('/api/state', token=waiter['token'])
        self.assertIsNone(waiting['state']); self.assertEqual(waiting['controlled'], [])
        status, error = self.request('/api/action', {'version': waiting['version'], 'action': {'type': 'pass'}}, waiter['token'])
        self.assertEqual(error['code'], 'SEAT_REQUIRED')
        status, error = self.request('/api/seat/confirm', {'seat': 0}, waiter['token'])
        self.assertEqual(error['code'], 'SEAT_TAKEN')
        status, error = self.request('/api/room/ai', {'seat': 0, 'enabled': False}, host['token'])
        self.assertEqual(error['code'], 'SEATS_LOCKED')

    def test_active_vacancy_can_be_replaced_by_local_ai_and_recorded_as_bot(self):
        clients = [self.enter('Player ' + str(i)) for i in range(4)]
        for i, c in enumerate(clients): self.confirm(c, i)
        self.request('/api/room/start', {}, clients[0]['token'])
        self.request('/api/leave', {}, clients[2]['token'])
        _, state = self.request('/api/state', token=clients[0]['token'])
        self.assertTrue(state['paused'])
        status, state = self.request('/api/room/ai', {'seat': 2, 'enabled': True}, clients[0]['token'])
        self.assertEqual(status, 200); self.assertFalse(state['paused'])
        self.assertTrue(state['players'][2]['bot'])
        room = self.server.registry.lookup(clients[0]['token'])
        policy = RandomPolicy(19)
        with room.condition:
            for _ in range(1200):
                if room.env.phase in ('round_end', 'match_end'): break
                if room.bidding_deadline is not None:
                    room.close_bidding_if_due(room.bidding_deadline);continue
                room.step(policy.act(room.env.observe(room.env.current_player)))
            else: self.fail('round did not finish')
            meta = next(r for r in self.server.registry.history.listing()['records'] if r['id'] == room.replay_id)
            self.assertTrue(meta['players'][2]['bot'])

    def test_pvp_ai_takes_a_real_turn_then_waits_for_human(self):
        host = self.enter(banker=0)
        self.confirm(host, 3)
        self.request('/api/room/ai', {'enabled': True}, host['token'])
        self.request('/api/room/start', {}, host['token'])
        room = self.server.registry.lookup(host['token'])
        deadline = time.monotonic() + 2
        while room.env.revision == 0 and time.monotonic() < deadline: time.sleep(.02)
        self.assertEqual(room.env.revision, 1)
        self.assertEqual(room.env.current_player, 3)
        self.assertEqual(room.env.action_log[-1]['player'], 0)
        _, state = self.request('/api/state', token=host['token'])
        self.assertNotIn('hands', state['state']); self.assertIsNone(state['state']['kitty'])

    def test_pvp_ai_banker_confirms_terminal_review(self):
        host = self.enter(banker=0)
        self.confirm(host, 1)
        self.request('/api/room/ai', {'enabled': True}, host['token'])
        self.request('/api/room/start', {}, host['token'])
        room = self.server.registry.lookup(host['token'])
        policy = RandomPolicy(19)
        with room.condition:
            for _ in range(1200):
                if room.env.phase in ('round_end', 'match_end'): break
                if room.bidding_deadline is not None:
                    room.close_bidding_if_due(room.bidding_deadline);continue
                room.step(policy.act(room.env.observe(room.env.current_player)))
            review = room.snapshot(host['token'])['settlement']
            self.assertTrue(review['ai_banker']); self.assertFalse(review['confirmed'])
            room.review_started -= AI_REVIEW_SECONDS
        deadline = time.monotonic() + 2
        while room.review_confirmed_by is None and time.monotonic() < deadline: time.sleep(.02)
        self.assertEqual(room.review_confirmed_by, 0)

    def test_default_pvp_bots_are_separate_enoch_policies(self):
        room = Room('default', 'pvp', GameConfig())
        host = room.join('Host')
        with room.condition:
            room.confirm_seat(host, 1); room.set_ai(host, None, True)
            self.assertTrue(all(isinstance(p, EnochPolicy) for p in room.policies))
            self.assertEqual(len({id(p) for p in room.policies}), 4)
            self.assertEqual(room.snapshot(host)['ai']['id'], 'enoch-table-v1')


if __name__ == '__main__': unittest.main()
