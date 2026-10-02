import copy
import json
import random
import unittest
from itertools import combinations
from tractor_sim import TractorEnv, GameConfig, RandomPolicy, IllegalAction
from tractor_sim.cards import face, power, effective_suit, MANDATORY
from tractor_sim.rules import (describe, components, validate_play, structure_profile,
                              matched_max, throw_succeeds, minimal_unit, follow_witness)
from tractor_sim.rules import forced_follow


def c(s, r, pair=False):
    f = {"S": 0, "H": 1, "D": 2, "C": 3}[s] * 13 + r - 2
    return [f, f + 54] if pair else [f]


def fixture(hands, level=6, trump="D", banker=0):
    env = TractorEnv(GameConfig(level, False, banker), seed=1)
    env.phase = "playing"; env.level = level; env.trump = trump
    env.hands = copy.deepcopy(hands); env.current_player = banker
    used = [x for h in hands for x in h]
    rest = [x for x in range(108) if x not in used]
    env.kitty = rest[:8]; env.played = rest[8:]; env.deal_count = 100
    env.assert_invariants()
    return env


def buried_env(seed=0):
    """A real dealt round with a single level bid, preserving all hand sizes."""
    env=TractorEnv(GameConfig(2,False,0),seed)
    while env.phase=='dealing':
        options=env.bid_options(env.current_player)
        env.step(options[0] if options and env.bid is None else {'type':'pass'})
    while env.phase=='final_bidding':env.step({'type':'pass'})
    assert env.phase=='burying'
    env.step({'type':'bury','cards':env.hands[0][:8]})
    return env


def dealt_bidding_env(banker=0, bidder=0, bid_cards=None, reserved=None, confirm=True, auction=False):
    """Deal a deterministic 108-card fixture through actual public actions."""
    bid_cards = bid_cards or c('H', 2)
    hands = copy.deepcopy(reserved or [[], [], [], []])
    for card in bid_cards:
        if card not in hands[bidder]: hands[bidder].append(card)
    used = [card for hand in hands for card in hand]
    assert len(used) == len(set(used))
    remaining = iter(card for card in range(108) if card not in used)
    for hand in hands:
        hand.extend(next(remaining) for _ in range(25 - len(hand)))
    deck = [hands[(banker + p) % 4][i] for i in range(25) for p in range(4)] + list(remaining)
    env = TractorEnv(GameConfig(2, auction, banker), seed=0)
    env.deck = deck; env.kitty = deck[100:]; env.hands = [[], [], [], []]
    env.hands[banker] = deck[:1]
    while env.phase == 'dealing':
        can_bid = env.current_player == bidder and env.bid is None and all(card in env.hands[bidder] for card in bid_cards)
        env.step({'type': 'bid', 'cards': bid_cards} if can_bid else {'type': 'pass'})
    if confirm:
        while env.phase=='final_bidding':env.step({'type':'pass'})
    return env


class CardRules(unittest.TestCase):
    def test_removed_level_adjacency(self):
        self.assertEqual(describe(c('S',5,True)+c('S',7,True),6,'D')['kind'],'tractor')
        self.assertEqual(describe(c('S',5,True)+c('S',8,True),6,'D')['kind'],'throw')

    def test_trump_level_joker_chain(self):
        cards=c('D',14,True)+c('S',6,True)+c('D',6,True)+[52,106,53,107]
        self.assertEqual(describe(cards,6,'D')['units'][0]['pairs'],5)
        self.assertEqual(describe(c('S',6,True)+c('H',6,True),6,'D')['kind'],'throw')
        self.assertEqual(describe(c('S',6,True)+[52,106],6,None)['kind'],'tractor')

    def test_distinct_level_cards_not_pair(self):
        self.assertEqual(describe(c('S',6)+c('H',6),6,'D')['kind'],'throw')
        self.assertEqual(power(c('S',6)[0],6,'D'),power(c('H',6)[0],6,'D'))

    def test_three_tractor_requires_two_tractor_plus_pair(self):
        lead=c('S',8,True)+c('S',9,True)+c('S',10,True)
        hand=c('S',3,True)+c('S',4,True)+c('S',12,True)+c('S',13)+c('S',14)
        good=c('S',3,True)+c('S',4,True)+c('S',12,True)
        validate_play(hand,good,lead,6,'D')
        with self.assertRaisesRegex(IllegalAction,'最长拖拉机'):
            validate_play(hand,c('S',3,True)+c('S',12,True)+c('S',13)+c('S',14),lead,6,'D')
        self.assertIsNone(matched_max(good,components(lead,6,'D'),6,'D'))

    def test_must_follow_pair(self):
        hand=c('S',3,True)+c('S',4)+c('S',8)
        with self.assertRaises(IllegalAction):
            validate_play(hand,c('S',4)+c('S',8),c('S',10,True),6,'D')

    def test_follow_door_not_printed_suit(self):
        hand=c('S',6)+c('S',3)+c('H',4)
        validate_play(hand,c('H',4),c('H',8),6,'D')
        with self.assertRaises(IllegalAction):
            validate_play(hand,c('S',6),c('S',8),6,'D')

    def test_not_enough_suit_requires_all(self):
        hand=c('S',3)+c('H',4)+c('H',8)
        with self.assertRaises(IllegalAction):
            validate_play(hand,c('H',4)+c('H',8),c('S',10,True),6,'D')
        validate_play(hand,c('S',3)+c('H',4),c('S',10,True),6,'D')

    def test_mixed_lead_rejected(self):
        with self.assertRaises(IllegalAction):
            validate_play(c('S',3)+c('H',3),c('S',3)+c('H',3),None,6,'D')

    def test_comparison_only_largest_pattern(self):
        units=components(c('D',8,True)+[53],6,'D')
        low_pair_big_single=c('D',8,True)+[53]
        high_pair_low_single=c('D',9,True)+c('D',3)
        self.assertGreater(matched_max(high_pair_low_single,units,6,'D'),matched_max(low_pair_big_single,units,6,'D'))
        tied=c('D',8,True)+[52]
        self.assertEqual(matched_max(tied,units,6,'D'),matched_max(low_pair_big_single,units,6,'D'))

    def test_full_match_required_even_when_max_strong(self):
        units=components(c('S',8,True)+c('S',9,True)+c('S',12),6,'D')
        self.assertIsNone(matched_max(c('D',8,True)+c('D',12,True)+[53],units,6,'D'))

    def test_largest_tractor_ignores_stronger_pair_and_single(self):
        low=c('D',8,True)+c('D',9,True)+c('D',13,True)+[53]
        high=c('D',10,True)+c('D',11,True)+c('D',3,True)+c('D',4)
        units=components(low,6,'D')
        self.assertGreater(matched_max(high,units,6,'D'),matched_max(low,units,6,'D'))

    def test_equal_length_allocations_preserve_later_obligations(self):
        # Runs 3-4 and 8-9-10: choose 3-4 first to leave a second two-pair run.
        hand=c('S',3,True)+c('S',4,True)+c('S',8,True)+c('S',9,True)+c('S',10,True)
        self.assertEqual(structure_profile(hand,(2,2),6,'D'),(2,0,2,0))
        selected=c('S',3,True)+c('S',4,True)+c('S',8,True)+c('S',10,True)
        lead=c('S',11,True)+c('S',12,True)+c('S',3,True)+c('S',4,True)
        with self.assertRaises(IllegalAction):validate_play(hand,selected,lead,6,'D')

    def test_large_single_throw_comparison(self):
        hand=c('D',3)+c('D',4)+c('D',5)+sum((c('D',r) for r in range(7,15)),[])+c('S',6)+c('H',6)+c('C',6)+c('D',6)+[52,53]
        self.assertEqual(matched_max(hand,components(hand,6,'D'),6,'D'),power(53,6,'D'))

    def test_throw_same_door_teammate_counts(self):
        attempt=c('S',12,True)+c('S',3)
        self.assertFalse(throw_succeeds(attempt,[c('S',14),[],[]],6,'D'))
        self.assertTrue(throw_succeeds(c('S',14,True)+c('S',13),[c('D',6),[],[]],6,'D'))

    def test_minimum_pair_inside_tractor(self):
        attempt=c('S',3,True)+c('S',4,True)+c('S',13)
        self.assertEqual(minimal_unit(attempt,6,'D'),c('S',3,True))

    def test_witness_many_follows(self):
        rng=random.Random(12)
        for level,trump in ((2,'S'),(6,'D'),(11,None),(14,'H')):
            for _ in range(40):
                hand=rng.sample(range(108),25)
                lead=c('C',8,True)+c('C',9,True) if level not in (8,9) else c('C',3,True)
                selected=follow_witness(hand,lead,level,trump,rng)
                validate_play(hand,selected,lead,level,trump)

    def test_forced_follow_respects_suit_pairs_and_tractors(self):
        cases=[
            (c('S',3,True),c('S',8),[3]),
            (c('S',3,True)+c('S',4),c('S',8),None),
            (c('S',3,True)+c('S',4)+c('S',5),c('S',8,True),[3,3]),
            (c('S',3,True)+c('S',4,True)+c('S',10),c('S',8,True)+c('S',9,True),[3,3,4,4]),
            (c('S',3)+c('H',4,True),c('S',8,True),[3,4]),
            (c('S',3)+c('H',4)+c('H',5),c('S',8,True),None),
        ]
        from tractor_sim.cards import rank
        for hand,lead,expected in cases:
            with self.subTest(hand=hand,lead=lead):
                selected=forced_follow(hand,lead,6,'D')
                self.assertEqual(sorted(map(rank,selected)) if selected is not None else None,expected)
                if selected is not None:validate_play(hand,selected,lead,6,'D')

    def test_forced_follow_matches_exhaustive_face_combinations(self):
        rng=random.Random(371)
        pool=sum((c(s,r,True) for s in ('S','H') for r in (3,4,5,7)),[])
        leads=[c('S',8),c('S',8,True),c('S',8,True)+c('S',12),
               c('S',8,True)+c('S',9,True),c('S',8,True)+c('S',9,True)+c('S',10,True)]
        for _ in range(120):
            hand=rng.sample(pool,rng.randint(6,10));lead=rng.choice(leads)
            legal=set()
            for selected in combinations(hand,len(lead)):
                try:validate_play(hand,list(selected),lead,6,'D')
                except IllegalAction:continue
                legal.add(tuple(sorted(map(face,selected))))
            forced=forced_follow(hand,lead,6,'D')
            self.assertEqual(forced is not None,len(legal)==1)
            if forced is not None:self.assertEqual(tuple(sorted(map(face,forced))),next(iter(legal)))

    def test_forced_follow_result_is_not_mutable_cache_data(self):
        hand=c('S',3,True);lead=c('S',8)
        selected=forced_follow(hand,lead,6,'D');selected.append(999)
        self.assertNotIn(999,forced_follow(hand,lead,6,'D'))


class EngineRules(unittest.TestCase):
    def test_illegal_actions_atomic(self):
        env=TractorEnv(seed=0); before=env.export_state()
        for action,player in (({'type':'play','cards':[]},0),({'type':'pass'},3),({'type':'bid','cards':[53,107]},0)):
            with self.assertRaises(IllegalAction):env.step(action,player)
            self.assertEqual(env.export_state(),before)

    def test_auction_pass_redeals(self):
        env=TractorEnv(seed=0)
        for _ in range(100):env.step({'type':'pass'})
        self.assertEqual((env.round_no,env.redeals,env.phase,env.deal_count),(1,1,'dealing',1))

    def test_no_auction_no_bid_public_bottom(self):
        env=TractorEnv(GameConfig(6,False,2),seed=0)
        for _ in range(100):env.step({'type':'pass'})
        self.assertEqual(env.phase,'playing');self.assertEqual(env.current_player,2)
        self.assertTrue(env.kitty_public);self.assertEqual(list(map(len,env.hands)),[25]*4)
        self.assertEqual(env.observe(1)['kitty'],env.kitty)

    def test_first_joker_bid_forbidden(self):
        env=TractorEnv();env.hands[env.current_player]=[53,107]
        self.assertEqual(env.validate_action({'type':'bid','cards':[53,107]})['code'],'FIRST_BID_JOKER')

    def test_counter_keeps_banker_and_first_lead(self):
        env=TractorEnv(GameConfig(2,False,0),seed=0)
        policy=RandomPolicy(2)
        while env.phase=='dealing':env.step(policy.act(env.observe(env.current_player)))
        while env.phase=='final_bidding':env.step({'type':'pass'})
        self.assertEqual(env.phase,'burying')
        env.bid={'strength':1,'player':0,'cards':c('S',2)}
        env.step({'type':'bury','cards':env.hands[0][:8]})
        # Move big jokers from other locations into seat 1, retaining all 108 ids.
        for card in (53,107):
            for h in env.hands:
                if card in h:h.remove(card)
            if card in env.kitty:env.kitty.remove(card)
            env.hands[1].append(card)
        while len(env.kitty)<8:
            card=env.hands[2].pop();env.kitty.append(card)
        env.current_player=1
        env.step({'type':'bid','cards':[53,107]})
        self.assertEqual(env.banker,0);self.assertEqual(env.current_player,1);self.assertEqual(env.phase,'burying')
        env.step({'type':'bury','cards':env.hands[1][:8]})
        self.assertEqual((env.phase,env.current_player),('playing',0))

    def test_counter_starts_next_and_excludes_bury_owner(self):
        for banker in range(4):
            with self.subTest(banker=banker):
                env=dealt_bidding_env(banker,banker)
                env.step({'type':'bury','cards':env.hands[banker][-8:]})
                expected=[(banker+i)%4 for i in range(1,4)]
                self.assertEqual(env.observe(banker)['counter_players'],expected)
                asked=[]
                while env.phase=='countering':
                    asked.append(env.current_player);env.step({'type':'pass'})
                self.assertEqual(asked,expected)
                self.assertEqual((env.phase,env.current_player),('playing',banker))

    def test_original_bidder_is_skipped_after_different_player_buries(self):
        # A=0 shows hearts; B=1 takes/buries. A cannot counter its own hearts.
        env=dealt_bidding_env(1,0,reserved=[c('H',2,True)+[52,106],[],[],[]])
        env.step({'type':'bury','cards':env.hands[1][-8:]})
        self.assertEqual(env.counter_queue,[2,3])
        before=env.export_state()
        self.assertFalse(env.validate_action({'type':'bid','cards':[52,106]},0)['valid'])
        self.assertFalse(env.validate_action({'type':'bid','cards':c('H',2,True)},0)['valid'])
        self.assertEqual(env.export_state(),before)
        env.step({'type':'pass'});self.assertEqual(env.current_player,3)
        env.step({'type':'pass'})
        self.assertEqual((env.phase,env.current_player),('playing',1))

    def test_successive_counters_restart_at_next_seat(self):
        env=dealt_bidding_env(reserved=[c('H',2),c('S',2,True),[52,106],[]])
        env.step({'type':'bury','cards':env.hands[0][-8:]})
        env.step({'type':'bid','cards':c('S',2,True)})
        env.step({'type':'bury','cards':env.hands[1][-8:]})
        self.assertEqual(env.counter_queue,[2,3,0])
        env.step({'type':'bid','cards':[52,106]})
        env.step({'type':'bury','cards':env.hands[2][-8:]})
        self.assertEqual(env.counter_queue,[3,0,1])
        while env.phase=='countering':env.step({'type':'pass'})
        self.assertEqual((env.phase,env.current_player),('playing',0))

    def test_each_counter_picks_up_latest_burial_not_original_bottom(self):
        env=dealt_bidding_env(reserved=[c('H',2),c('S',2,True),[52,106],[53,107]])
        original=env.deck[100:]
        self.assertEqual(env.known_kitties[0][0],original)
        latest=[card for card in env.hands[0] if card not in original][:8]
        self.assertNotEqual(set(latest),set(original))
        env.step({'type':'bury','cards':latest})
        for player,bid in ((1,c('S',2,True)),(2,[52,106]),(3,[53,107])):
            before=env.hands[player][:]
            env.step({'type':'bid','cards':bid})
            self.assertEqual(env.current_player,player)
            self.assertEqual(len(env.hands[player]),33)
            self.assertEqual(set(env.hands[player])-set(before),set(latest))
            self.assertEqual(env.known_kitties[player][-1],latest)
            self.assertEqual(env.kitty,[])
            self.assertNotEqual(set(latest),set(original))
            latest=before[:8]
            env.step({'type':'bury','cards':latest})
            self.assertEqual(env.kitty,latest)
            self.assertEqual(len(env.hands[player]),25)
            env.assert_invariants()
        self.assertEqual((env.phase,env.current_player),('playing',0))

    def test_final_bidding_asks_one_circle_before_original_banker_pickup(self):
        for banker in range(4):
            env=dealt_bidding_env(banker,banker,confirm=False)
            self.assertEqual(env.phase,'final_bidding')
            self.assertEqual(list(map(len,env.hands)),[25]*4)
            self.assertEqual(len(env.kitty),8);self.assertIsNone(env.kitty_owner)
            asked=[]
            while env.phase=='final_bidding':
                asked.append(env.current_player)
                self.assertIsNone(env.observe(env.current_player)['drawn_card'])
                env.step({'type':'pass'})
            self.assertEqual(asked,[(banker+i)%4 for i in range(4)])
            self.assertEqual((env.phase,env.current_player),('burying',banker))

    def test_final_bidding_counter_takes_bottom_but_fixed_banker_keeps_lead(self):
        env=dealt_bidding_env(reserved=[c('H',2),c('S',2,True),[],[]],confirm=False)
        original=env.kitty[:];before=env.hands[1][:]
        env.step({'type':'pass'});env.step({'type':'bid','cards':c('S',2,True)})
        self.assertEqual((env.phase,env.current_player,env.banker),('burying',1,0))
        self.assertEqual(set(env.hands[1])-set(before),set(original))
        env.step({'type':'bury','cards':env.hands[1][-8:]})
        self.assertEqual(env.current_player,2)
        while env.phase=='countering':env.step({'type':'pass'})
        self.assertEqual((env.phase,env.current_player),('playing',0))

    def test_banker_can_strengthen_own_bid_before_picking_bottom(self):
        env=dealt_bidding_env(reserved=[c('H',2,True),[],[],[]],confirm=False)
        action={'type':'bid','cards':c('H',2,True)}
        self.assertTrue(env.validate_action(action)['valid'])
        env.step(action)
        self.assertEqual((env.phase,env.current_player,env.bid['strength']),('burying',0,2))
        env.step({'type':'bury','cards':env.hands[0][-8:]})
        self.assertNotIn(0,env.counter_queue)

    def test_final_bidding_counter_in_auction_also_takes_banker(self):
        env=dealt_bidding_env(reserved=[c('H',2),c('S',2,True),[],[]],confirm=False,auction=True)
        env.step({'type':'pass'});env.step({'type':'bid','cards':c('S',2,True)})
        self.assertEqual((env.phase,env.current_player,env.banker),('burying',1,1))

    def test_final_bidding_checkpoint_preserves_remaining_seats(self):
        env=dealt_bidding_env(confirm=False);env.step({'type':'pass'})
        restored=TractorEnv.from_state(json.loads(json.dumps(env.export_state())))
        self.assertEqual(restored.final_bid_queue,[1,2,3])
        for _ in range(3):env.step({'type':'pass'});restored.step({'type':'pass'})
        self.assertEqual(restored.export_state(),env.export_state())

    def test_drawn_card_is_private_and_cleared_when_next_player_draws(self):
        env=TractorEnv(seed=0)
        first=env.current_player
        self.assertEqual(env.observe(first)['drawn_card'],env.deck[0])
        for p in range(4):
            if p!=first:self.assertIsNone(env.observe(p,omniscient=True)['drawn_card'])
        env.step({'type':'pass'})
        self.assertIsNone(env.observe(first)['drawn_card'])
        self.assertEqual(env.observe(env.current_player)['drawn_card'],env.deck[1])

    def test_big_joker_counter_skips_questions_after_rebury(self):
        env=dealt_bidding_env(reserved=[c('H',2),[],[],[53,107]])
        env.step({'type':'bury','cards':env.hands[0][-8:]})
        env.step({'type':'pass'});env.step({'type':'pass'})
        env.step({'type':'bid','cards':[53,107]})
        self.assertEqual(env.bid['strength'],4)
        transition=env.step({'type':'bury','cards':env.hands[3][-8:]})
        self.assertEqual((env.phase,env.current_player),('playing',0))
        self.assertIsNone(env.trump);self.assertEqual(env.counter_queue,[])
        self.assertEqual(transition.observation['bid_options'],[])

    def test_big_jokers_shown_during_deal_skip_all_counter_questions(self):
        env=dealt_bidding_env(reserved=[c('H',2),[53,107],[],[]])
        # Replay the prescribed deal, allowing seat 1 to show its pair on receipt.
        state=env.export_state()
        env=TractorEnv(GameConfig(2,False,0),seed=0)
        env.deck=state['deck'];env.kitty=env.deck[100:];env.hands=[[env.deck[0]],[],[],[]]
        while env.phase=='dealing':
            action={'type':'pass'}
            if env.current_player==0 and env.bid is None:action={'type':'bid','cards':c('H',2)}
            elif env.current_player==1 and env.bid['strength']<4 and all(x in env.hands[1] for x in (53,107)):
                action={'type':'bid','cards':[53,107]}
            env.step(action)
        env.step({'type':'bury','cards':env.hands[0][-8:]})
        self.assertEqual((env.phase,env.current_player),('playing',0))

    def test_counter_checkpoint_preserves_remaining_seats(self):
        env=dealt_bidding_env(1,0)
        env.step({'type':'bury','cards':env.hands[1][-8:]});env.step({'type':'pass'})
        restored=TractorEnv.from_state(json.loads(json.dumps(env.export_state())))
        self.assertEqual(restored.counter_queue,[3])
        restored.step({'type':'pass'});env.step({'type':'pass'})
        self.assertEqual(restored.export_state(),env.export_state())

    def test_hidden_information_redacted(self):
        env=TractorEnv(seed=0)
        obs=env.observe(1)
        self.assertNotIn('hands',obs);self.assertNotIn('deck',obs);self.assertIsNone(obs['kitty'])
        obs['hand'].append(999);self.assertNotIn(999,env.hands[1])

    def test_forced_play_only_for_current_follower_and_keeps_rng(self):
        env=fixture([c('S',8),c('S',3,True),c('H',5),c('C',7)])
        self.assertIsNone(env.observe(0)['forced_play'])
        env.step({'type':'play','cards':c('S',8)})
        before=env.export_state()
        selected=env.observe(1)['forced_play']
        self.assertEqual(list(map(face,selected)),list(map(face,c('S',3))))
        self.assertIsNone(env.observe(0,omniscient=True)['forced_play'])
        self.assertEqual(env.export_state(),before)
        self.assertTrue(env.validate_action({'type':'play','cards':selected})['valid'])

    def test_teaching_mode_does_not_reveal_undealt_bottom(self):
        env=TractorEnv(seed=0)
        for player in range(4):
            obs=env.observe(player,omniscient=True)
            self.assertIn('hands',obs);self.assertIsNone(obs['kitty'])

    def test_bury_owner_only_has_private_bottom_memory(self):
        env=buried_env()
        for player in range(4):
            for teaching in (False,True):
                self.assertIsNone(env.observe(player,omniscient=teaching)['kitty'])
        self.assertEqual(env.observe(0)['known_kitties'][-1],env.kitty)
        self.assertEqual(env.observe(1)['known_kitties'],[])
        while env.phase=='countering':env.step({'type':'pass'})
        self.assertIsNone(env.observe(0,omniscient=True)['kitty'])

    def test_counter_hides_new_bottom_preserving_private_memories(self):
        # Find an actual dealt hand that can counter, without relocating cards.
        for seed in range(16):
            env=buried_env(seed)
            original=env.kitty[:]
            counter=None
            while env.phase=='countering':
                options=env.bid_options(env.current_player)
                if options and env.current_player!=0:
                    counter=env.current_player;env.step(options[-1]);break
                env.step({'type':'pass'})
            if counter is not None:break
        self.assertIsNotNone(counter)
        replacement=[c for c in env.hands[counter] if c not in original][:8]
        env.step({'type':'bury','cards':replacement})
        self.assertNotEqual(replacement,original)
        self.assertEqual(env.observe(0)['known_kitties'][-1],original)
        self.assertEqual(env.observe(counter)['known_kitties'][-1],replacement)
        for player in range(4):self.assertIsNone(env.observe(player,omniscient=True)['kitty'])

    def test_normal_settlement_and_step_info_keep_bottom_hidden(self):
        env=buried_env()
        policy=RandomPolicy(10)
        while env.phase not in ('round_end','match_end'):
            transition=env.step(policy.act(env.observe(env.current_player)))
        self.assertFalse(env.kitty_public)
        self.assertIsNone(env.result['kitty'])
        self.assertIsNone(transition.info['result']['kitty'])
        self.assertIsNone(next(e for e in transition.info['events'] if e['type']=='round_ended')['kitty'])
        for player in range(4):self.assertIsNone(env.observe(player,omniscient=True)['kitty'])
        env.step({'type':'next_round'})
        self.assertIsNone(env.observe(0)['result']['kitty'])

    def test_flipped_bottom_remains_public_in_teaching_and_settlement(self):
        env=TractorEnv(GameConfig(2,False,0),seed=0)
        for _ in range(100):env.step({'type':'pass'})
        policy=RandomPolicy(11)
        while env.phase not in ('round_end','match_end'):
            env.step(policy.act(env.observe(env.current_player)))
        self.assertTrue(env.kitty_public)
        for player in range(4):
            self.assertEqual(env.observe(player,omniscient=True)['kitty'],env.kitty)
        self.assertEqual(env.result['kitty'],env.kitty)

    def test_legacy_checkpoint_redacts_hidden_settlement(self):
        env=buried_env();policy=RandomPolicy(12)
        while env.phase!='round_end':env.step(policy.act(env.observe(env.current_player)))
        state=env.export_state()
        state['result']['kitty']=env.kitty[:]
        state['events'][-1]['kitty']=env.kitty[:]
        restored=TractorEnv.from_state(state)
        obs=restored.observe(0,omniscient=True)
        self.assertIsNone(obs['kitty']);self.assertIsNone(obs['result']['kitty'])
        self.assertIsNone(obs['events'][-1]['kitty'])

    def test_throw_validation_does_not_peek(self):
        env=fixture([c('S',3)+c('S',8),c('S',4)+c('H',3),c('C',3)+c('C',4),c('H',4)+c('H',8)])
        action={'type':'play','cards':env.hands[0][:]}
        self.assertTrue(env.validate_action(action)['valid'])
        env.step(action)
        self.assertEqual(env.penalty_score,20);self.assertEqual(env.trick[0]['cards'],c('S',3))
        self.assertIn(c('S',8)[0],env.hands[0])

    def test_attacker_throw_penalty_negative(self):
        env=fixture([c('H',3)+c('H',4),c('S',3)+c('S',8),c('S',4)+c('C',3),c('C',4)+c('H',8)])
        env.current_player=1
        env.step({'type':'play','cards':env.hands[1][:]})
        self.assertEqual(env.penalty_score,-20)

    def test_legal_actions_include_failed_throws_without_mutation(self):
        env=fixture([c('S',3,True)+c('S',8,True),c('S',4,True),c('H',3,True),c('C',3,True)])
        before=env.export_state()
        actions=list(env.iter_legal_actions())
        self.assertEqual(len(actions),8)  # Counts 0..2 of each face, except both zero.
        self.assertTrue(any(set(a['cards'])==set(env.hands[0]) for a in actions))
        self.assertEqual(env.export_state(),before)
        env.step({'type':'play','cards':env.hands[0][:]})
        self.assertEqual(env.penalty_score,40)

    def test_pair_beats_strong_single_in_compound(self):
        env=fixture([c('D',8,True)+[53],c('D',9,True)+c('D',3),c('H',3,True)+c('H',4),c('C',3,True)+c('C',4)])
        env.trick=[{'player':p,'cards':h} for p,h in enumerate(env.hands)]
        self.assertEqual(env._trick_winner(),1)

    def test_equal_max_does_not_compare_remaining(self):
        # Separate rule test: comparator same top pair, single king size ignored.
        u=components(c('D',8,True)+[52],6,'D')
        self.assertEqual(matched_max(c('D',8,True)+[53],u,6,'D'),matched_max(c('D',8,True)+[52],u,6,'D'))

    def test_complete_ruff_required_and_earlier_equal_trump_wins(self):
        env=fixture([c('S',8,True)+c('S',9,True)+c('S',12),
                     c('D',8,True)+c('D',12,True)+[53],
                     c('D',9,True)+c('D',10,True)+c('D',3),
                     c('H',3,True)+c('H',4,True)+c('H',8)])
        env.trick=[{'player':p,'cards':h} for p,h in enumerate(env.hands)]
        self.assertEqual(env._trick_winner(),2)
        # Equal off-level pairs are distinct faces; earlier complete ruff wins.
        env=fixture([c('S',8,True),c('H',6,True),c('C',6,True),c('H',8,True)])
        env.trick=[{'player':p,'cards':h} for p,h in enumerate(env.hands)]
        self.assertEqual(env._trick_winner(),1)

    def test_bottom_multipliers_and_j_in_losing_hand_ignored(self):
        for winning,multiplier in ((c('D',3),2),(c('D',3,True),4),
                                  (c('D',3,True)+c('D',4,True),8),
                                  (c('D',3,True)+c('D',4,True)+c('D',5,True),16),
                                  (c('D',3,True)+c('D',8,True)+c('D',9)+[52,53],8)):
            env=fixture([c('S',3),winning,c('H',3),c('C',3)],level=11)
            env.kitty=c('H',5)+c('H',10)+c('H',13)+c('C',5)+c('C',10)+c('C',13)+c('S',7)+c('S',8)
            env.last_trick={'plays':[{'player':0,'cards':c('S',11)},{'player':1,'cards':winning}]}
            env._settle(1)
            self.assertEqual(env.result['multiplier'],multiplier)
            self.assertEqual(env.result['bottom'],50*multiplier)
            self.assertIsNone(env.result['downgrade'])

    def test_score_boundaries(self):
        for score,team,amount in ((-20,0,3),(0,0,3),(1,0,2),(39,0,2),(40,0,1),(79,0,1),(80,1,0),(119,1,0),(120,1,1),(160,1,2),(200,1,3)):
            env=fixture([c('S',3),c('H',3),c('D',3),c('C',3)],level=6)
            env.captured_score=env.score=score
            env.last_trick={'plays':[{'player':p,'cards':h} for p,h in enumerate(env.hands)]}
            env._settle(0)
            self.assertEqual((env.result['upgrading_team'],env.result['upgrade']),(team,amount))
            self.assertEqual(env.banker,2 if team==0 else 1)

    def test_mandatory_cannot_skip_and_records_permanent(self):
        env=TractorEnv(GameConfig(3,False,0));env.levels=[3,3]
        self.assertEqual(env._advance(0,3),5)
        env.passed[0].add(5);self.assertEqual(env._advance(0,3),6)
        env.levels[0]=11;env.passed[0].update((2,5,10,11))
        env.levels[0]=2
        self.assertEqual(env._advance(0,5),7)

    def test_j_and_a_downgrade_by_winners_cards_only(self):
        for level,target in ((11,2),(14,11)):
            env=fixture([c('S',3),c('D',level),c('S',4),c('S',5)],level=level)
            env.passed[0]=set(MANDATORY)
            for p in range(4):env.step({'type':'play','cards':env.hands[env.current_player][:]})
            self.assertEqual(env.result['downgrade'],target)
            self.assertEqual(env.levels[0],target);self.assertEqual(env.passed[0],set(MANDATORY))
            self.assertIsNone(env.match_winner)

    def test_a_defending_win_ends_match(self):
        env=fixture([[53],c('S',3),c('S',4),c('S',5)],level=14)
        for _ in range(4):env.step({'type':'play','cards':env.hands[env.current_player][:]})
        self.assertEqual(env.phase,'match_end');self.assertEqual(env.match_winner,0)

    def test_snapshot_json_roundtrip_and_replay(self):
        env=TractorEnv(seed=6);p=RandomPolicy(8)
        for _ in range(120):env.step(p.act(env.observe(env.current_player)))
        restored=TractorEnv.from_state(json.loads(json.dumps(env.export_state())))
        for _ in range(30):
            if env.phase=='match_end':break
            action=p.act(env.observe(env.current_player));env.step(action);restored.step(action)
        self.assertEqual(env.export_state(),restored.export_state())

    def test_complete_random_games_and_levels(self):
        for level in (2,5,6,10,11,13,14):
            for seed in range(4):
                env=TractorEnv(GameConfig(level,seed%2==0,seed%4),seed);policy=RandomPolicy(seed+50)
                for step in range(1000):
                    env.step(policy.act(env.observe(env.current_player)))
                    if env.phase in ('round_end','match_end'):break
                else:self.fail('对局未结束')
                env.assert_invariants()

    def test_compound_throw_rollouts(self):
        rng=random.Random(20261002)
        for seed in range(20):
            env=TractorEnv(GameConfig(2+seed%13,False,seed%4),seed);policy=RandomPolicy(seed)
            for step in range(1000):
                obs=env.observe(env.current_player)
                action=policy.act(obs)
                if env.phase=='playing' and not env.trick and rng.random()<0.7:
                    groups={}
                    for card in obs['hand']:
                        groups.setdefault(effective_suit(card,env.level,env.trump),[]).append(card)
                    group=rng.choice(list(groups.values()))
                    action={'type':'play','cards':rng.sample(group,rng.randint(1,min(10,len(group))))}
                env.step(action)
                if env.phase in ('round_end','match_end'):break
            else:self.fail('甩牌对局未结束')


if __name__=='__main__':unittest.main()
