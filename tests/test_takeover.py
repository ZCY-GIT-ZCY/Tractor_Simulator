"""Seat ownership, private observations and cancellation of asynchronous AI control."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tractor_sim.enoch import EnochPolicy
from tractor_sim.engine import GameConfig
from tractor_sim.policy import RandomPolicy
from tractor_sim.rules import IllegalAction
from tractor_sim.server import Registry, Room, TractorServer
from test_rules import c, dealt_bidding_env, fixture

ROOT = Path(__file__).resolve().parent.parent


def seated_room(factory=None):
    room = Room('takeover_fixture', 'pvp', GameConfig(2, False, 0), 7, factory)
    tokens = [room.join('Player ' + str(seat)) for seat in range(4)]
    with room.condition:
        for seat, token in enumerate(tokens):
            room.confirm_seat(token, seat)
        room.start_room(tokens[0])
    return room, tokens


class SeatControl(unittest.TestCase):
    def test_takeover_keeps_owner_private_cards_and_uses_existing_enoch(self):
        room, tokens = seated_room()
        with room.condition:
            before = room.env.export_state()
            room.set_takeover(tokens[1], True)
            own, other = room.snapshot(tokens[1]), room.snapshot(tokens[0])
            self.assertTrue(own['takeover']); self.assertFalse(other['takeover'])
            self.assertTrue(other['players'][1]['takeover'])
            self.assertEqual(other['players'][1]['name'], 'Player 1')
            self.assertFalse(other['players'][1]['bot'])
            self.assertEqual(own['controlled'], [1]); self.assertFalse(room.autoplay)
            self.assertNotIn('hands', own['state']); self.assertNotIn('deck', own['state'])
            self.assertEqual(before, room.env.export_state())
            self.assertIsInstance(room.policies[1], EnochPolicy)
            self.assertEqual(own['ai']['id'], EnochPolicy.id)
            epoch, version = room.policy_epochs[1], room.version
            room.set_takeover(tokens[1], True)
            self.assertEqual((room.policy_epochs[1], room.version), (epoch, version))
            room.set_takeover(tokens[1], False)
            self.assertFalse(room.snapshot(tokens[0])['players'][1]['takeover'])

    def test_manual_actions_require_cancellation_and_return_immediately_after_it(self):
        room, tokens = seated_room(RandomPolicy)
        with room.condition:
            self.assertEqual(room.env.current_player, 0)
            room.set_takeover(tokens[0], True)
            self.assertEqual(Registry._policy_actor(room), 0)
            with self.assertRaises(IllegalAction) as error:
                room.can_act(tokens[0], {'type': 'pass'})
            self.assertEqual(error.exception.code, 'AI_CONTROLLED')
            room.set_takeover(tokens[0], False)
            self.assertEqual(room.can_act(tokens[0], {'type': 'pass'}), 0)
            self.assertIsNone(Registry._policy_actor(room))
            room.step({'type': 'pass'}, 0)
            self.assertEqual(room.env.deal_count, 2)

    def test_delegated_disconnection_continues_but_manual_disconnection_pauses(self):
        room, tokens = seated_room()
        with room.condition:
            room.set_takeover(tokens[0], True)
            room.clients[tokens[0]]['seen'] -= 40
            room.refresh_pause(); self.assertFalse(room.paused)
            room.clients[tokens[1]]['seen'] -= 40
            room.refresh_pause(); self.assertTrue(room.paused)
            room.check(tokens[1]); room.refresh_pause(); self.assertFalse(room.paused)
            room.set_takeover(tokens[0], False)
            room.clients[tokens[0]]['seen'] -= 40
            room.refresh_pause(); self.assertTrue(room.paused)

    def test_delegated_human_banker_still_confirms_the_final_bottom_review(self):
        room, tokens = seated_room()
        with room.condition:
            room.env = fixture([c('S', 4), c('H', 4), c('D', 4), c('C', 4)],
                               level=3, trump='H', banker=0)
            room.set_takeover(tokens[0], True)
            while room.env.phase == 'playing':
                actor = room.env.current_player
                room.step({'type':'play', 'cards':room.env.hands[actor][:]}, actor)
            review = room.settlement(room.clients[tokens[0]])
            self.assertTrue(review['can_confirm']); self.assertFalse(review['ai_banker'])
            self.assertFalse(review['confirmed'])
            room.confirm_result(tokens[0])
            self.assertTrue(room.settlement(room.clients[tokens[0]])['confirmed'])

    def test_round_three_self_setter_is_skipped_then_asked_after_small_joker_counter(self):
        # Same bidding sequence as the reported hand: banker 2, initial bidder
        # 1 owns both big jokers, banker partner 0 owns both small jokers.
        env = dealt_bidding_env(banker=2, bidder=1, level=3, bid_cards=c('S', 3),
                               reserved=[[52, 106], c('S', 3) + [53, 107], [], []])
        env.step({'type': 'bury', 'cards': env.hands[2][-8:]})
        self.assertEqual((env.current_player, env.counter_queue), (0, [0, 3]))
        env.step({'type': 'bid', 'cards': [52, 106]}, 0)
        env.step({'type': 'bury', 'cards': [v for v in env.hands[0] if v not in (52, 106)][:8]})
        self.assertEqual(env.counter_queue, [3, 2, 1])
        env.step({'type': 'pass'}); env.step({'type': 'pass'})
        self.assertEqual(env.current_player, 1)
        env.step({'type': 'bid', 'cards': [53, 107]})
        self.assertEqual((env.phase, env.kitty_owner, env.bid['strength']), ('burying', 1, 4))
        env.step({'type': 'bury', 'cards': [v for v in env.hands[1] if v not in (53, 107)][:8]})
        self.assertEqual((env.phase, env.current_player), ('playing', 2))


class TakeoverAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
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
            headers={'Content-Type': 'application/json',
                     **({'Authorization': 'Bearer ' + token} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as reply: return reply.status, json.load(reply)
        except urllib.error.HTTPError as error: return error.code, json.load(error)

    def test_only_caller_seat_is_delegated_and_all_peers_see_it(self):
        clients = []
        for seat in range(4):
            status, client = self.request('/api/session', {'mode': 'pvp', 'room': 'takeover_http',
                'name': 'Human ' + str(seat), 'seed': 7, 'config': {'auction': False, 'banker': 2}})
            self.assertEqual(status, 200)
            self.assertEqual(self.request('/api/seat/confirm', {'seat': seat}, client['token'])[0], 200)
            clients.append(client)
            if seat == 0:
                _, waiter = self.request('/api/session', {'name': '测试玩家', 'mode': 'pvp', 'room': 'takeover_http'})
        self.assertEqual(self.request('/api/room/start', {}, clients[0]['token'])[0], 200)
        for value in ('true', 1, None, [], {}):
            self.assertEqual(self.request('/api/autoplay', {'enabled': value}, clients[1]['token'])[0], 400)
        status, own = self.request('/api/autoplay', {'enabled': True, 'seat': 2}, clients[1]['token'])
        self.assertEqual(status, 200); self.assertTrue(own['takeover'])
        _, peer = self.request('/api/state', token=clients[0]['token'])
        self.assertEqual([p['seat'] for p in peer['players'] if p['takeover']], [1])
        self.assertFalse(peer['takeover']); self.assertFalse(peer['autoplay'])
        self.assertEqual(peer['players'][1]['name'], 'Human 1')
        status, error = self.request('/api/validate', {'action': {'type': 'pass'}}, clients[1]['token'])
        self.assertEqual((status, error['code']), (422, 'AI_CONTROLLED'))
        status, error = self.request('/api/autoplay', {'enabled': True}, waiter['token'])
        self.assertEqual((status, error['code']), (422, 'SEAT_REQUIRED'))
        status, own = self.request('/api/autoplay', {'enabled': False}, clients[1]['token'])
        self.assertEqual(status, 200); self.assertFalse(own['takeover'])
        _, peer = self.request('/api/state', token=clients[0]['token'])
        self.assertFalse(any(p['takeover'] for p in peer['players']))


class AsyncTakeover(unittest.TestCase):
    def test_cancel_and_reenable_cannot_apply_an_old_inflight_decision(self):
        entered, release = threading.Event(), threading.Event()
        observations = []
        class SlowPolicy:
            def __init__(self, seed): self.calls = 0
            def act(self, obs):
                observations.append(obs); self.calls += 1
                if self.calls == 1:
                    entered.set(); release.wait(4)
                return {'type': 'pass'}
        (ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as directory:
            registry = Registry(policy_factory=SlowPolicy, history_dir=Path(directory) / 'history')
            clients = [registry.enter({'name': '测试玩家', 'mode': 'pvp', 'room': 'async_takeover',
                'seed': 7, 'config': {'auction': False, 'banker': 0}}) for _ in range(4)]
            room = registry.lookup(clients[0]['token'])
            with room.condition:
                for seat, client in enumerate(clients): room.confirm_seat(client['token'], seat)
                room.start_room(clients[0]['token']); room.set_takeover(clients[0]['token'], True)
                room.next_bot_at = 0; revision = room.env.revision
            worker = threading.Thread(target=registry.run_bots); worker.start()
            try:
                self.assertTrue(entered.wait(2))
                with room.condition:
                    room.set_takeover(clients[0]['token'], False)
                    self.assertEqual(room.can_act(clients[0]['token'], {'type': 'pass'}), 0)
                    room.set_takeover(clients[0]['token'], True)
                    room.next_bot_at = time.monotonic() + 60
                release.set(); deadline = time.monotonic() + 2
                while room.pending is not None and time.monotonic() < deadline: time.sleep(.01)
                self.assertIsNone(room.pending); self.assertEqual(room.env.revision, revision)
                self.assertFalse(room.policy_error)
                with room.condition: room.next_bot_at = 0
                deadline = time.monotonic() + 2
                while room.env.revision == revision and time.monotonic() < deadline: time.sleep(.01)
                self.assertEqual(room.env.revision, revision + 1)
                self.assertEqual(room.env.deal_count, 2)
                self.assertTrue(observations)
                self.assertTrue(all('hands' not in obs and 'deck' not in obs and obs['player'] == 0
                                    for obs in observations))
            finally:
                release.set(); registry.stopped.set(); worker.join(5)


if __name__ == '__main__': unittest.main()
