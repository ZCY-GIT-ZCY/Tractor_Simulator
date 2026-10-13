import unittest

from test_rules import c, fixture
from tractor_sim.history import frame
from tractor_sim.rules import IllegalAction


def move(env, cards):
    result = env.step({'type': 'play', 'cards': cards})
    event = next(e for e in result.info['events'] if e['type'] == 'play')
    return event


class PublicPlayNotices(unittest.TestCase):
    def single_env(self):
        return fixture([c('S', 9) + c('C', 4), c('H', 14) + c('C', 5),
                        c('H', 7) + c('C', 6), c('H', 8) + c('C', 7)], level=2, trump='H')

    def test_ruff_under_ruff_and_overruff_follow_the_actual_winner(self):
        env = self.single_env()
        self.assertEqual(move(env, c('S', 9))['notices'], [])
        self.assertIsNone(env.observe(0)['trick_winner'])
        second = move(env, c('H', 8))
        self.assertEqual(second['notices'], ['ruff'])
        self.assertEqual(second['winner'], 3)
        self.assertEqual(env.observe(0)['trick_winner'], 3)
        third = move(env, c('H', 7))
        self.assertEqual(third['notices'], [])
        self.assertEqual(third['winner'], 3)
        fourth = move(env, c('H', 14))
        self.assertEqual(fourth['notices'], ['overruff'])
        self.assertEqual(fourth['winner'], env.last_trick['winner'])
        self.assertEqual(env.last_trick['winner'], 1)
        self.assertEqual(fourth['trick_number'], 1)
        self.assertIsNone(env.observe(0)['trick_winner'])
        # A fresh lead is unmarked even though the previous four plays persist.
        self.assertEqual(move(env, c('C', 5))['trick_number'], 2)
        self.assertIsNone(env.observe(0)['trick_winner'])

    def test_only_leading_trumps_announce_trump_lead(self):
        env = fixture([c('H', 8), c('H', 11), c('H', 10), c('H', 9)], level=2, trump='H')
        self.assertEqual(move(env, c('H', 8))['notices'], ['trump_lead'])
        for cards in (c('H', 9), c('H', 10), c('H', 11)):
            self.assertEqual(move(env, cards)['notices'], [])

    def test_higher_side_suit_and_equal_cards_are_not_kills(self):
        env = fixture([c('S', 9), c('S', 7), c('S', 8), c('S', 14)], level=2, trump='H')
        move(env, c('S', 9))
        self.assertEqual(move(env, c('S', 14))['notices'], [])
        self.assertEqual(env.observe(0)['trick_winner'], 3)
        env = fixture([c('S', 9), c('S', 7), c('S', 8), [c('S', 9)[0] + 54]], level=2, trump='H')
        move(env, c('S', 9))
        self.assertEqual(move(env, [c('S', 9)[0] + 54])['notices'], [])
        self.assertEqual(env.observe(0)['trick_winner'], 0)

    def test_unmatched_all_trump_follow_does_not_announce_kill(self):
        env = fixture([c('S', 9, True), c('C', 3, True), c('C', 4, True),
                       c('H', 8) + c('H', 14)], level=2, trump='H')
        move(env, c('S', 9, True))
        event = move(env, c('H', 8) + c('H', 14))
        self.assertEqual(event['notices'], [])
        self.assertEqual(env.observe(0)['trick_winner'], 0)

    def test_valid_throw_and_pair_trumps_used_as_singles_can_kill(self):
        env = fixture([c('S', 8) + c('S', 14), c('C', 3, True), c('C', 4, True),
                       c('H', 8, True)], level=2, trump='H')
        self.assertEqual(move(env, c('S', 8) + c('S', 14))['notices'], ['throw'])
        event = move(env, c('H', 8, True))
        self.assertEqual(event['notices'], ['ruff'])
        self.assertEqual(event['winner'], 3)

    def test_failed_throw_still_announces_throw_and_uses_forced_cards(self):
        env = fixture([c('S', 8) + c('S', 14), c('S', 9) + c('C', 3),
                       c('C', 4, True), c('C', 5, True)], level=2, trump='H')
        event = move(env, c('S', 8) + c('S', 14))
        self.assertEqual(event['notices'], ['throw'])
        self.assertEqual(event['cards'], c('S', 8))
        self.assertEqual(env.events[-2]['type'], 'throw_failed')

    def test_trump_throw_announces_both_and_replay_keeps_partial_winner(self):
        env = fixture([c('H', 8) + c('H', 14), c('H', 3) + c('H', 4),
                       c('H', 5) + c('H', 6), c('C', 3, True)], level=2, trump='H')
        self.assertEqual(move(env, c('H', 8) + c('H', 14))['notices'], ['throw', 'trump_lead'])
        move(env, c('C', 3, True))
        recorded = frame(env)
        self.assertEqual(recorded['state']['trick_winner'], 0)
        for player in range(4):
            observed = env.observe(player)
            self.assertEqual(observed['trick_winner'], 0)
            self.assertNotIn('hands', observed)

    def test_invalid_action_does_not_create_any_notification(self):
        env = self.single_env()
        move(env, c('S', 9))
        before = env.export_state()
        with self.assertRaises(IllegalAction):
            env.step({'type': 'play', 'cards': c('H', 7)})  # Wrong physical owner.
        self.assertEqual(env.export_state(), before)

    def test_six_single_throw_killed_by_three_pair_tractor_is_still_single_bottom(self):
        lead = sum((c('S', r) for r in (3, 5, 7, 9, 11, 13)), [])
        low = sum((c('H', r, True) for r in (3, 4, 5)), [])
        high = sum((c('H', r, True) for r in (6, 7, 8)), [])
        side = sum((c('C', r) for r in (3, 4, 5, 6, 7, 8)), [])
        env = fixture([lead, high, side, low], level=2, trump='H')
        self.assertEqual(move(env, lead)['notices'], ['throw'])
        self.assertEqual(move(env, low)['notices'], ['ruff'])
        self.assertEqual(move(env, side)['notices'], [])
        self.assertEqual(move(env, high)['notices'], ['overruff'])
        self.assertEqual(env.result['multiplier'], 2)
