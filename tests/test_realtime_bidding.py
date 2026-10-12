import copy
import unittest
import threading
import time

from tractor_sim import TractorEnv, GameConfig, IllegalAction
from tractor_sim.cards import points
from test_rules import c, fixture, dealt_bidding_env
from tractor_sim.server import Room, Registry


def waiting_deal():
    env = dealt_bidding_env(reserved=[c('H', 2, True), c('S', 2, True), [53,107], [52,106]],
                            confirm=False, auction=True)
    # All 100 cards dealt, before the last drawer chooses to finish drawing.
    env.phase = 'dealing'; env.current_player = 1
    env.bid = None; env.bid_history = []; env.trump = None; env.final_bid_queue = []
    env.assert_invariants()
    return env


class RealtimeBidding(unittest.TestCase):
    def test_offturn_bid_does_not_draw_or_change_drawer(self):
        env = waiting_deal(); before = copy.deepcopy(env.hands)
        self.assertIn({'type':'bid','cards':c('H',2)}, list(env.iter_legal_actions(0)))
        env.step({'type':'bid','cards':c('H',2)}, player=0)
        self.assertEqual((env.current_player,env.deal_count,env.phase), (1,100,'dealing'))
        self.assertEqual(env.hands,before)
        self.assertTrue(env.observe(2)['bid_options'])
        self.assertEqual(env.validate_action({'type':'pass'},0)['code'],'NOT_YOUR_TURN')
        env.step({'type':'pass'},1)
        self.assertEqual(env.phase,'final_bidding')

    def test_only_same_suit_level_pair_can_reinforce_self(self):
        env = waiting_deal()
        env.step({'type':'bid','cards':c('H',2)},0)
        for cards in ([52,106], [53,107]):
            # Ownership does not influence the self-counter rule being tested.
            owner = next(p for p,h in enumerate(env.hands) if cards[0] in h)
            for card in cards:
                env.hands[owner].remove(card); env.hands[0].append(card)
            before = env.export_state()
            with self.assertRaises(IllegalAction) as error:
                env.step({'type':'bid','cards':cards},0)
            self.assertEqual(error.exception.code,'SELF_COUNTER')
            self.assertEqual(env.export_state(),before)
            for card in cards:
                env.hands[0].remove(card); env.hands[owner].append(card)
        env.step({'type':'bid','cards':c('H',2,True)},0)
        self.assertEqual(env.observe(0)['bid_options'],[])
        env.step({'type':'bid','cards':[52,106]},3)
        self.assertEqual(env.observe(3)['bid_options'],[])
        env.step({'type':'pass'},1)
        self.assertNotIn(3,env.final_bid_queue)

    def test_changing_own_suit_is_not_reinforcement(self):
        env = waiting_deal()
        cards=c('S',2,True)
        for card in cards: env.hands[1].remove(card); env.hands[0].append(card)
        env.step({'type':'bid','cards':c('H',2)},0)
        self.assertEqual(env.validate_action({'type':'bid','cards':cards},0)['code'],'SELF_COUNTER')

    def test_equal_later_bid_rejected_without_mutation(self):
        env = waiting_deal()
        env.step({'type':'bid','cards':c('H',2)},0)
        before=env.export_state()
        with self.assertRaises(IllegalAction) as error:
            env.step({'type':'bid','cards':c('S',2)},1)
        self.assertEqual(error.exception.code,'BID_TOO_WEAK')
        self.assertEqual(env.export_state(),before)
        env.step({'type':'bid','cards':c('S',2,True)},1)
        self.assertEqual(env.bid['player'],1)

    def test_after_dealing_bids_are_still_polled(self):
        env=waiting_deal();env.step({'type':'bid','cards':c('H',2)},0)
        env.step({'type':'pass'},1)
        self.assertEqual(env.validate_action({'type':'bid','cards':[53,107]},2)['code'],'NOT_YOUR_TURN')
        self.assertEqual(env.observe(2)['bid_options'],[])

    def test_no_bid_auction_redeals_but_normal_reveals_bottom(self):
        env=TractorEnv(GameConfig(2,True,0),7)
        for _ in range(100): env.step({'type':'pass'})
        self.assertEqual((env.phase,env.deal_count,env.redeals,env.round_no),('dealing',1,1,1))
        env=TractorEnv(GameConfig(6,False,2),7); bottom=env.kitty[:]
        for _ in range(100): env.step({'type':'pass'})
        self.assertEqual((env.phase,env.current_player,env.kitty_owner),('playing',2,None))
        self.assertTrue(env.kitty_public);self.assertEqual(env.kitty,bottom)
        self.assertEqual(list(map(len,env.hands)),[25]*4)


class BottomLeadStructure(unittest.TestCase):
    def test_six_distinct_singles_ruffed_by_three_pair_tractor_still_x2(self):
        lead=sum((c('C',r) for r in range(3,9)),[])
        ruff=sum((c('S',r,True) for r in range(7,10)),[])
        env=fixture([lead,sum((c('H',r) for r in range(9,15)),[]),
                     sum((c('D',r) for r in range(9,15)),[]),ruff],level=2,trump='S')
        for cards in (lead,ruff,env.hands[2][:],env.hands[1][:]):
            env.step({'type':'play','cards':cards})
        self.assertEqual(env.last_trick['winner'],3)
        self.assertEqual(len(env.last_trick['plays'][0]['cards']),6)
        self.assertEqual(env.result['multiplier'],2)
        self.assertEqual(env.bottom_score,sum(map(points,env.kitty))*2)

    def test_pair_joker_ruff_of_single_throw_only_doubles_bottom(self):
        env=fixture([c('C',14)+c('C',9),c('H',3,True),c('D',3)+[52],[53,107]],
                    level=2,trump='S')
        for cards in (c('C',14)+c('C',9),[53,107],c('D',3)+[52],c('H',3,True)):
            env.step({'type':'play','cards':cards})
        self.assertEqual(env.last_trick['winner'],3)
        self.assertEqual(env.result['multiplier'],2)
        self.assertEqual(env.bottom_score,sum(map(points,env.kitty))*2)

    def test_extra_pair_in_single_part_does_not_raise_mixed_throw_bottom(self):
        lead=c('C',14,True)+c('C',9)+c('C',8)
        ruff=c('S',14,True)+[53,107]
        env=fixture([lead,c('H',3,True)+c('H',4,True),c('D',3,True)+c('D',4,True),ruff],
                    level=2,trump='S')
        for cards in (lead,ruff,env.hands[2][:],env.hands[1][:]):
            env.step({'type':'play','cards':cards})
        self.assertEqual(env.last_trick['winner'],3)
        self.assertEqual(env.result['multiplier'],4)


class BiddingGraceClock(unittest.TestCase):
    def room(self, auction=True):
        room=Room('clock_fixture','self',GameConfig(2,auction,0),seed=0)
        room.join('clock player',0)
        room.condition.acquire()
        self.addCleanup(room.condition.release)
        room.env=waiting_deal();room.env.auction=auction
        return room

    def test_clock_starts_at_card_100_and_closes_once_without_last_pass(self):
        room=self.room();env=room.env
        env.hands[1].remove(env.deck[99]);env.deal_count=99;env.current_player=2
        env.assert_invariants()
        room.sync_bidding_window(10);self.assertIsNone(room.bidding_deadline)
        room.step({'type':'pass'},2,now=10)
        self.assertEqual((env.deal_count,room.bidding_deadline),(100,16))
        before=env.export_state()
        with self.assertRaises(IllegalAction) as e:room.step({'type':'pass'},1,now=15.99)
        self.assertEqual(e.exception.code,'BIDDING_WAIT');self.assertEqual(env.export_state(),before)
        self.assertFalse(room.close_bidding_if_due(15.999))
        self.assertTrue(room.close_bidding_if_due(16))
        self.assertEqual((env.phase,env.deal_count,env.redeals),('dealing',1,1))
        revision=env.revision
        self.assertFalse(room.close_bidding_if_due(17));self.assertEqual(env.revision,revision)

    def test_last_moment_bid_and_counter_do_not_restart_clock(self):
        room=self.room();room.sync_bidding_window(100)
        room.step({'type':'bid','cards':c('H',2)},0,now=105.8)
        room.step({'type':'bid','cards':[52,106]},3,now=105.99)
        self.assertEqual(room.bidding_deadline,106)
        self.assertFalse(room.close_bidding_if_due(105.999))
        self.assertTrue(room.close_bidding_if_due(106))
        self.assertEqual(room.env.phase,'final_bidding')
        self.assertNotIn(3,room.env.final_bid_queue)
        self.assertEqual(room.env.bid['strength'],3)
        self.assertEqual(list(map(len,room.env.hands)),[25]*4)

    def test_bid_at_or_after_deadline_is_rejected(self):
        room=self.room(False);room.sync_bidding_window(100)
        before=room.env.export_state()
        with self.assertRaises(IllegalAction) as e:room.step({'type':'bid','cards':c('H',2)},0,now=106)
        self.assertEqual(e.exception.code,'BIDDING_CLOSED');self.assertEqual(room.env.export_state(),before)
        self.assertTrue(room.close_bidding_if_due(106))
        self.assertEqual(room.env.phase,'playing');self.assertTrue(room.env.kitty_public)
        self.assertEqual(room.env.kitty_owner,None)

    def test_disconnect_pauses_clock_and_resume_keeps_remaining_time(self):
        room=self.room();room.sync_bidding_window(100)
        room.paused=True;room.sync_bidding_window(102)
        self.assertFalse(room.close_bidding_if_due(140))
        room.paused=False;room.sync_bidding_window(142)
        self.assertEqual(room.bidding_deadline,146)
        self.assertFalse(room.close_bidding_if_due(145.99))
        self.assertTrue(room.close_bidding_if_due(146))

    def test_automatic_no_bid_normal_round_reveals_original_bottom(self):
        room=self.room(False);bottom=room.env.kitty[:]
        room.sync_bidding_window(100);room.close_bidding_if_due(106)
        self.assertEqual(room.env.kitty,bottom);self.assertTrue(room.env.kitty_public)
        self.assertEqual(list(map(len,room.env.hands)),[25]*4)
        self.assertEqual((room.env.phase,room.env.current_player),('playing',0))

    def test_maximum_bid_still_waits_then_picks_up_once(self):
        room=self.room();room.sync_bidding_window(100)
        room.step({'type':'bid','cards':c('H',2)},0,now=101)
        room.step({'type':'bid','cards':[53,107]},2,now=102)
        self.assertEqual((room.env.phase,len(room.env.hands[2])),('dealing',25))
        self.assertTrue(room.close_bidding_if_due(106))
        self.assertEqual((room.env.phase,room.env.kitty_owner,len(room.env.hands[2])),('burying',2,33))
        self.assertFalse(room.close_bidding_if_due(107))


class GraceAsyncPolicies(unittest.TestCase):
    def test_ai_can_bid_during_grace_when_last_drawer_is_human(self):
        class BidPolicy:
            def __init__(self,seed):pass
            def act(self,obs):return obs['bid_options'][0] if obs['bid_options'] else {'type':'pass'}
        registry=Registry(policy_factory=BidPolicy)
        s=registry.enter({'mode':'pve','seat':1});room=registry.lookup(s['token'])
        with room.condition:
            room.env=waiting_deal();room.sync_bidding_window();room.next_bot_at=0
        thread=threading.Thread(target=registry.run_bots);thread.start()
        try:
            deadline=time.monotonic()+2
            while room.env.bid is None and time.monotonic()<deadline:time.sleep(.01)
            self.assertIsNotNone(room.env.bid);self.assertNotEqual(room.env.bid['player'],1)
            self.assertEqual((room.env.current_player,room.env.deal_count),(1,100))
            self.assertFalse(room.policy_error)
        finally:registry.stopped.set();thread.join(4)

    def test_slow_ai_result_cannot_outlive_grace_or_block_automatic_close(self):
        entered,release=threading.Event(),threading.Event()
        class SlowPolicy:
            def __init__(self,seed):pass
            def act(self,obs):
                entered.set();release.wait(3)
                return obs['bid_options'][0] if obs['bid_options'] else {'type':'pass'}
        registry=Registry(policy_factory=SlowPolicy)
        s=registry.enter({'mode':'pve','seat':1});room=registry.lookup(s['token'])
        with room.condition:
            room.env=waiting_deal();room.sync_bidding_window();room.next_bot_at=0
        thread=threading.Thread(target=registry.run_bots);thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with room.condition:
                room.bidding_deadline=time.monotonic()-1
                self.assertTrue(room.close_bidding_if_due())
                room.next_bot_at=time.monotonic()+30
                revision=room.env.revision
            release.set();deadline=time.monotonic()+2
            while room.pending is not None and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(room.env.revision,revision);self.assertIsNone(room.env.bid)
            self.assertFalse(room.policy_error)
            self.assertEqual((room.env.redeals,room.env.deal_count),(1,1))
        finally:release.set();registry.stopped.set();thread.join(4)


if __name__=='__main__':unittest.main()
