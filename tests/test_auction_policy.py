import unittest

from tractor_sim import TractorEnv, GameConfig
from tractor_sim.enoch import EnochPolicy
from test_rules import c, dealt_bidding_env


def prefix_deal(prefix, level=2, auction=True):
    env = TractorEnv(GameConfig(level, auction, 0), 0)
    env.deck = prefix + [card for card in range(108) if card not in prefix]
    env.kitty = env.deck[100:]
    env.hands = [[prefix[0]], [], [], []]
    while env.deal_count < len(prefix):
        env.step({'type': 'pass'})
    env.assert_invariants()
    return env


class OpeningAuctionPolicy(unittest.TestCase):
    def test_single_two_declares_with_an_otherwise_weak_one_card_hand(self):
        env = prefix_deal(c('S', 2))
        action = EnochPolicy(0, time_ms=0).act(env.observe(0))
        self.assertEqual(action, {'type': 'bid', 'cards': c('S', 2)})
        env.step(action)
        self.assertEqual(env.banker, 0)

    def test_self_reinforcement_is_not_blocked_by_hand_strength(self):
        env = prefix_deal([0, 39, 40, 41, 54])
        env.step({'type': 'bid', 'cards': c('S', 2)})
        action = EnochPolicy(0, time_ms=0).act(env.observe(0))
        self.assertEqual(action, {'type': 'bid', 'cards': c('S', 2, True)})
        env.step(action)
        self.assertEqual(env.bid['strength'], 2)

    def test_flower_pair_small_jokers_and_big_jokers_all_counter(self):
        env = prefix_deal([0, 13, 52, 53, 1, 67, 106, 107])
        env.step({'type': 'bid', 'cards': [0]}, 0)
        policy = EnochPolicy(0, time_ms=0)
        for actor, cards, strength in [(3, c('H', 2, True), 2), (2, [52, 106], 3), (1, [53, 107], 4)]:
            action = policy.act(env.observe(actor))
            self.assertEqual(action, {'type': 'bid', 'cards': cards})
            self.assertTrue(env.validate_action(action, actor)['valid'])
            env.step(action, actor)
            self.assertEqual((env.banker, env.bid['strength']), (actor, strength))
        self.assertEqual(policy.act(env.observe(1)), {'type': 'pass'})

    def test_jokers_cannot_open_and_player_cannot_counter_own_suit(self):
        env = prefix_deal([52, 39, 40, 41, 106])
        self.assertEqual(EnochPolicy(0).act(env.observe(0)), {'type': 'pass'})
        env = prefix_deal([0, 39, 40, 41, 53, 42, 43, 44, 107])
        env.step({'type': 'bid', 'cards': [0]}, 0)
        self.assertEqual(EnochPolicy(0).act(env.observe(0)), {'type': 'pass'})

    def test_final_auction_always_uses_available_legal_raise(self):
        env = dealt_bidding_env(reserved=[c('H', 2), c('S', 2, True), [52, 106], [53, 107]],
                                confirm=False, auction=True)
        decisions = 0
        while env.phase == 'final_bidding':
            actor = env.current_player
            options = env.bid_options(actor)
            action = EnochPolicy(0, time_ms=0).act(env.observe(actor))
            self.assertEqual(action['type'], 'bid' if options else 'pass')
            env.step(action, actor)
            decisions += 1
            self.assertLess(decisions, 12)
        self.assertEqual(env.phase, 'burying')

    def test_normal_round_and_other_level_keep_the_existing_strength_gate(self):
        for level, auction in [(2, False), (6, True)]:
            env = prefix_deal(c('S', level), level=level, auction=auction)
            self.assertEqual(EnochPolicy(0, time_ms=0).act(env.observe(0)), {'type': 'pass'})
