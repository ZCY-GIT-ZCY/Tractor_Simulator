import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from tractor_sim.checkpoint import load_registry
from tractor_sim.policy import RandomPolicy
from tractor_sim.rules import IllegalAction
from tractor_sim.server import Registry, TractorServer


class RoomExpiry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = Registry(policy_factory=RandomPolicy, history_dir=Path(self.tmp.name))
        self.clock = patch('tractor_sim.server.time.monotonic', return_value=1000)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def enter(self, mode='pvp', code='expire'):
        result = self.registry.enter({'mode': mode, 'room': code, 'name': '真人'})
        return self.registry.lookup(result['token']), result['token']

    def page(self, room, token, page='page_0001'):
        with room.condition: room.check(token, page)

    def test_pvp_and_pve_expire_at_one_minute_and_invalidate_tokens(self):
        for mode in ('pvp', 'pve'):
            room, token = self.enter(mode)
            room.updated = 9999  # AI updates cannot extend room lifetime.
            self.now.return_value = 1059.99
            self.registry.prune()
            self.assertIs(self.registry.lookup(token), room)
            self.now.return_value = 1060
            self.registry.prune()
            self.assertNotIn(room.code, self.registry.rooms)
            self.assertTrue(room.retired)
            with self.assertRaises(IllegalAction): self.registry.lookup(token)
            with self.assertRaises(IllegalAction): room.snapshot(token)
            self.now.return_value = 1000

    def test_online_takeover_human_or_waiting_member_keeps_room(self):
        room, first = self.enter()
        _, second = self.enter(code=room.code)
        self.page(room, first)
        self.page(room, second, 'page_0002')
        room.clients[first]['takeover'] = True
        self.now.return_value = 1090
        self.page(room, first)
        self.registry.prune()
        self.assertIn(room.code, self.registry.rooms)
        self.now.return_value = 1150
        self.registry.prune()
        self.assertNotIn(room.code, self.registry.rooms)

    def test_pve_autoplay_is_still_a_sixty_second_human_room(self):
        room, token = self.enter('pve')
        with room.condition: room.set_autoplay(True)
        self.page(room, token)
        self.registry.close_page(token, 'page_0001')
        self.now.return_value = 1006
        self.registry.prune()
        self.assertIn(room.code, self.registry.rooms)
        self.now.return_value = 1060
        self.registry.prune()
        self.assertNotIn(room.code, self.registry.rooms)

    def test_all_human_pvp_takeover_retains_one_minute_grace(self):
        room, token = self.enter()
        with room.condition:
            room.confirm_seat(token, 0); room.set_ai(token, None, True)
            room.start_room(token); room.set_takeover(token, True)
        self.page(room, token)
        self.registry.close_page(token, 'page_0001')
        self.now.return_value = 1006
        self.registry.prune()
        self.assertIn(room.code, self.registry.rooms)
        self.now.return_value = 1060
        self.registry.prune()
        self.assertNotIn(room.code, self.registry.rooms)

    def test_four_ai_demo_pagehide_reclaims_after_refresh_grace(self):
        room, token = self.enter('self')
        with room.condition: room.set_autoplay(True)
        self.page(room, token)
        self.registry.close_page(token, 'page_0001')
        self.now.return_value = 1003
        with room.condition: room.snapshot(token)  # Pending poll cannot revive it.
        self.registry.prune()
        self.assertIn(room.code, self.registry.rooms)
        self.now.return_value = 1005
        self.registry.prune()
        self.assertNotIn(room.code, self.registry.rooms)

    def test_refresh_and_second_tab_prevent_premature_ai_room_expiry(self):
        room, token = self.enter('self')
        with room.condition: room.set_autoplay(True)
        self.page(room, token)
        self.now.return_value = 1002
        self.page(room, token, 'page_0002')
        self.registry.close_page(token, 'page_0001')
        self.now.return_value = 1008
        self.registry.prune()
        self.assertIn(room.code, self.registry.rooms)
        self.registry.close_page(token, 'page_0002')
        self.now.return_value = 1013
        self.registry.prune()
        self.assertNotIn(room.code, self.registry.rooms)

    def test_ai_demo_browser_crash_uses_heartbeat_fallback(self):
        room, token = self.enter('self')
        with room.condition: room.set_autoplay(True)
        self.page(room, token)
        self.now.return_value = 1024.99
        self.registry.prune()
        self.assertIn(room.code, self.registry.rooms)
        self.now.return_value = 1025
        self.registry.prune()
        self.assertNotIn(room.code, self.registry.rooms)

    def test_unknown_duplicate_and_late_close_notices_are_harmless(self):
        room, token = self.enter('self')
        with room.condition: room.set_autoplay(True)
        self.page(room, token)
        self.registry.close_page(token, 'page_9999')
        self.assertTrue(room.presence(1000)[1])
        self.registry.close_page(token, 'page_0001')
        self.now.return_value = 1004
        self.registry.close_page(token, 'page_0001')
        self.now.return_value = 1005
        self.registry.prune()
        self.assertNotIn(room.code, self.registry.rooms)
        self.registry.close_page(token, 'page_0001')

    def test_old_client_without_page_header_can_coexist_with_a_modern_closed_tab(self):
        room, token = self.enter('self')
        with room.condition: room.set_autoplay(True)
        self.page(room, token)
        self.registry.close_page(token, 'page_0001')
        self.now.return_value = 1008
        with room.condition: room.check(token, browser_request=True)
        self.registry.prune()
        self.assertIn(room.code, self.registry.rooms)
        self.now.return_value = 1033
        self.registry.prune()
        self.assertNotIn(room.code, self.registry.rooms)

    def test_last_human_leaving_keeps_only_a_brief_rejoin_window(self):
        room, token = self.enter()
        with room.condition:
            room.confirm_seat(token, 0); room.set_ai(token, None, True)
        self.registry.leave(token)
        self.assertEqual(room.ai_seats, {0, 1, 2, 3})
        self.assertFalse(room.presence(1000)[1])
        self.now.return_value = 1003
        _, newcomer = self.enter(code=room.code)
        self.assertIs(self.registry.lookup(newcomer), room)
        self.registry.leave(newcomer)
        self.now.return_value = 1008
        self.registry.prune()
        self.assertNotIn(room.code, self.registry.rooms)

    def test_expiry_cancels_queued_work_and_invalidates_running_results(self):
        room, token = self.enter('pve')
        future = Future()
        room.pending = (future, room.env.revision, 1, 0)
        self.now.return_value = 1060
        self.registry.prune()
        self.assertTrue(future.cancelled())
        self.assertIsNone(room.pending)
        self.assertTrue(room.retired)
        self.assertNotIn(token, self.registry.tokens)

    def test_checkpoint_preserves_closed_pages_and_bounds_legacy_expiry(self):
        room, token = self.enter('self')
        with room.condition: room.set_autoplay(True)
        self.page(room, token)
        self.registry.close_page(token, 'page_0001')
        document = json.loads(json.dumps(self.registry.freeze()))
        document['idle_seconds'] = 7200
        self.now.return_value = 1200
        restored = Registry(history_dir=Path(self.tmp.name))
        load_registry(restored, document)
        self.assertEqual(restored.idle_seconds, 60)
        self.assertEqual(restored.lookup(token).clients[token]['pages']['page_0001']['closed_at'], 1200)
        restored.resume()
        self.now.return_value = 1205
        restored.prune()
        self.assertNotIn(room.code, restored.rooms)


class PagehideHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = TractorServer(('127.0.0.1', 0), policy_factory=RandomPolicy, history_dir=Path(cls.tmp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(2)
        cls.tmp.cleanup()

    def request(self, path, data=None, token=None, page=None):
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + token
        if page: headers['X-Tractor-Page'] = page
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as response: return response.status, json.load(response)
        except urllib.error.HTTPError as error: return error.code, json.load(error)

    def test_body_secret_only_authenticates_registered_page_close(self):
        status, state = self.request('/api/session', {'name': '测试玩家', 'mode': 'pve'}, page='page_http1')
        self.assertEqual(status, 200)
        token = state['token']
        status, denied = self.request('/api/action', {'token': token, 'version': state['version'], 'action': {'type': 'pass'}})
        self.assertEqual(status, 422); self.assertEqual(denied['code'], 'SESSION')
        self.assertEqual(self.request('/api/pagehide', {'token': token, 'page_id': 'page_http1'})[0], 200)
        room = self.server.registry.lookup(token)
        self.assertIsNotNone(room.clients[token]['pages']['page_http1']['closed_at'])
        self.assertEqual(self.request('/api/state', token=token, page='page_http2')[0], 200)
        self.assertTrue(room.presence(time.monotonic())[1])
        self.assertEqual(self.request('/api/pagehide', {'token': token, 'page_id': 'invalid!'})[0], 400)
        self.assertEqual(self.request('/api/pagehide', {'page_id': 'page_http2'})[0], 400)
        self.request('/api/leave', {}, token=token)

    def test_inflight_long_poll_cannot_reopen_closed_page(self):
        _, state = self.request('/api/session', {'name': '测试玩家', 'mode': 'pvp', 'room': 'pending_close'}, page='page_http1')
        token = state['token']; room = self.server.registry.lookup(token)
        before = room.clients[token]['pages']['page_http1']['seen']
        replies = []
        thread = threading.Thread(target=lambda: replies.append(self.request(f'/api/state?since={state["version"]}&wait=4', token=token, page='page_http1')))
        thread.start()
        deadline = time.monotonic() + 2
        while room.clients[token]['pages']['page_http1']['seen'] <= before and time.monotonic() < deadline: time.sleep(.005)
        self.request('/api/pagehide', {'token': token, 'page_id': 'page_http1'})
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(replies[0][0], 200)
        self.assertIsNotNone(room.clients[token]['pages']['page_http1']['closed_at'])
        self.assertFalse(room.presence(time.monotonic())[1])
        self.request('/api/leave', {}, token=token)
