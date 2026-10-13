import tempfile
import threading
import time
import unittest
from pathlib import Path

from tractor_sim.policy import RandomPolicy
from tractor_sim.server import Registry
from test_rules import c, fixture


class EmptyRoom(unittest.TestCase):
    def test_last_human_leaves_ai_finishes_one_hand_and_room_reopens(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = Registry(policy_factory=RandomPolicy, history_dir=Path(folder))
            clients = [registry.enter({'mode': 'pvp', 'room': 'last_leave', 'name': str(i)}) for i in range(4)]
            room = registry.lookup(clients[0]['token'])
            with room.condition:
                for seat, client in enumerate(clients): room.confirm_seat(client['token'], seat)
                room.start_room(clients[0]['token'])
                room.history = None
                room.env = fixture([c('S', r) for r in (3, 4, 5, 6)], level=2, trump='H')
            for client in clients: registry.leave(client['token'])
            self.assertEqual(room.ai_seats, {0, 1, 2, 3})
            self.assertIsNone(room.host); self.assertFalse(room.clients)
            worker = threading.Thread(target=registry.run_bots, daemon=True)
            worker.start()
            try:
                deadline = time.monotonic() + 5
                while room.env.phase != 'round_end' and time.monotonic() < deadline: time.sleep(.02)
                self.assertEqual(room.env.phase, 'round_end')
                self.assertEqual(len(room.env.action_log), 4)
                newcomer = registry.enter({'mode': 'pvp', 'room': 'last_leave', 'name': 'New'})
                with room.condition:
                    room.confirm_seat(newcomer['token'], 2)
                    self.assertEqual(room.host, newcomer['token'])
                    self.assertEqual(room.env.round_no, 1)
                    self.assertFalse(room.clients[newcomer['token']]['takeover'])
                    self.assertNotIn(2, room.ai_seats)
            finally:
                registry.stopped.set(); worker.join(5)
