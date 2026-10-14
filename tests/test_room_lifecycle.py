import tempfile
import threading
import time
import unittest
from pathlib import Path

from tractor_sim.policy import RandomPolicy
from tractor_sim.server import Registry
from test_rules import c, fixture


class EmptyRoom(unittest.TestCase):
    def test_last_human_leaves_stops_ai_and_abandoned_room_is_reclaimed(self):
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
                time.sleep(.15)
                self.assertEqual(room.env.phase, 'playing')
                self.assertEqual(len(room.env.action_log), 0)
                with room.condition: room.vacant_since -= 5
                registry.prune()
                self.assertNotIn(room.code, registry.rooms)
                newcomer = registry.enter({'mode': 'pvp', 'room': 'last_leave', 'name': 'New'})
                new_room = registry.lookup(newcomer['token'])
                self.assertIsNot(new_room, room)
                with new_room.condition:
                    new_room.confirm_seat(newcomer['token'], 2)
                    self.assertEqual(new_room.host, newcomer['token'])
                    self.assertFalse(new_room.clients[newcomer['token']]['takeover'])
            finally:
                registry.stopped.set(); worker.join(5)
