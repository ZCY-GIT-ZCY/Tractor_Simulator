import copy
import threading
import time
import unittest

from tractor_sim.engine import GameConfig
from tractor_sim.policy import RandomPolicy
from tractor_sim.rules import IllegalAction
from tractor_sim.server import Room, Registry, AI_REVIEW_SECONDS


class TableReviewTests(unittest.TestCase):
    def finish(self, room):
        policy = RandomPolicy(19)
        for _ in range(1200):
            if room.env.phase in ('round_end', 'match_end'):
                return
            if room.bidding_deadline is not None:
                with room.condition:room.close_bidding_if_due(room.bidding_deadline)
                continue
            room.step(policy.act(room.env.observe(room.env.current_player)))
        self.fail('round did not finish')

    def test_review_reveals_only_final_bottom_and_banker_confirms_for_everyone(self):
        room = Room('review', 'pvp', GameConfig(auction=False, banker=2), 18)
        clients = [room.join('Player ' + str(i)) for i in range(4)]
        with room.condition:
            for i, token in enumerate(clients): room.confirm_seat(token, i)
            room.start_room(clients[0])
            self.assertIsNone(room.snapshot(clients[0])['settlement'])
            self.assertIsNone(room.snapshot(clients[0])['state']['kitty'])
            self.finish(room)
            states = [room.snapshot(t) for t in clients]
            for state in states:
                self.assertFalse(state['settlement']['confirmed'])
                self.assertEqual(state['settlement']['kitty'], room.env.kitty)
                self.assertEqual(len(state['settlement']['kitty']), 8)
            self.assertEqual(states[2]['settlement']['banker'], 2)
            self.assertTrue(states[2]['settlement']['can_confirm'])
            self.assertFalse(states[0]['settlement']['can_confirm'])  # host is not this banker
            self.assertFalse(states[1]['settlement']['can_confirm'])
            before = copy.deepcopy(room.env.export_state())
            with self.assertRaises(IllegalAction) as error:
                room.confirm_result(clients[0])
            self.assertEqual(error.exception.code, 'BANKER_ONLY')
            with self.assertRaises(IllegalAction) as error:
                room.can_act(clients[0], {'type': 'next_round'})
            self.assertEqual(error.exception.code, 'REVIEW_PENDING')
            room.confirm_result(clients[2])
            self.assertEqual(room.env.export_state(), before)
            version = room.version
            room.confirm_result(clients[2])
            self.assertEqual(room.version, version)
            for t in clients:
                state = room.snapshot(t)
                self.assertTrue(state['settlement']['confirmed'])
                self.assertEqual(state['settlement']['confirmed_by'], 2)
            actor = room.can_act(clients[0], {'type': 'next_round'})
            room.step({'type': 'next_round'}, actor)
            self.assertIsNone(room.snapshot(clients[0])['settlement'])
            self.assertIsNone(room.review_confirmed_by)

    def test_self_mode_can_confirm_on_behalf_of_banker(self):
        room = Room('self_review', 'self', GameConfig(auction=False, banker=2), 18)
        token = room.join('Human', 0)
        with room.condition:
            with self.assertRaises(IllegalAction):
                room.confirm_result(token)
            self.finish(room)
            room.confirm_result(token)
            self.assertEqual(room.snapshot(token)['settlement']['confirmed_by'], 2)

    def test_ai_banker_waits_for_human_confirmation_even_after_timer_then_starts_next_hand(self):
        registry = Registry(policy_factory=RandomPolicy)
        room = Room('ai_review', 'pve', GameConfig(auction=False, banker=2), 18, RandomPolicy)
        token = room.join('Human', 0)
        registry.rooms[room.code] = room
        with room.condition:
            self.finish(room)
            before = copy.deepcopy(room.env.export_state())
            review = room.snapshot(token)['settlement']
            self.assertTrue(review['ai_banker'])
            self.assertFalse(review['confirmed'])
            self.assertTrue(review['can_confirm'])
            self.assertGreater(review['ai_confirm_seconds'], AI_REVIEW_SECONDS - 1)
        worker = threading.Thread(target=registry.run_bots, daemon=True)
        worker.start()
        try:
            time.sleep(.15)
            with room.condition:
                self.assertIsNone(room.review_confirmed_by)
                room.review_started -= AI_REVIEW_SECONDS
                self.assertFalse(room.finish_ai_review())
                self.assertEqual(room.env.export_state(), before)
                old_round=room.env.round_no
                room.confirm_result(token, old_round)
                self.assertEqual(room.env.round_no, old_round+1)
                self.assertEqual(room.env.phase, 'dealing')
        finally:
            registry.stopped.set(); worker.join(2)

    def test_human_banker_in_pve_is_never_auto_confirmed(self):
        room = Room('human_review', 'pve', GameConfig(auction=False, banker=0), 18, RandomPolicy)
        token = room.join('Human', 0)
        with room.condition:
            self.finish(room)
            room.review_started -= 100
            review = room.snapshot(token)['settlement']
            self.assertTrue(review['can_confirm'])
            self.assertFalse(review['ai_banker'])
            self.assertFalse(review['confirmed'])


if __name__ == '__main__':
    unittest.main()
