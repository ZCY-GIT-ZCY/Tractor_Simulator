import json
import unittest
from test_rules import c, fixture, dealt_bidding_env
from tractor_sim import TractorEnv, GameConfig
from tractor_sim.cards import face


class CurrentTableRules(unittest.TestCase):
    def test_every_dealer_draws_counterclockwise(self):
        for banker in range(4):
            env = TractorEnv(GameConfig(2, False, banker), 1)
            asked = []
            for _ in range(8):
                asked.append(env.current_player)
                env.step({'type': 'pass'})
            self.assertEqual(asked, [(banker-i) % 4 for i in range(8)])
            self.assertEqual(env.observe(env.current_player)['turn_direction'], -1)

    def test_play_order_is_south_east_north_west_then_winner(self):
        env = fixture([c('D',3)+c('H',3), c('D',6)+c('H',4),
                       c('D',5)+c('H',5), c('D',4)+c('H',6)], level=2, trump='S')
        actors = []
        for _ in range(4):
            p = env.current_player; actors.append(p)
            env.step({'type':'play', 'cards':[env.hands[p][0]]})
        self.assertEqual(actors, [0,3,2,1])
        self.assertEqual([p['player'] for p in env.last_trick['plays']], [0,3,2,1])
        self.assertEqual(env.current_player, 1)

    def test_two_auction_reinforcement_leaves_small_and_big_joker_chance(self):
        env = dealt_bidding_env(reserved=[c('H',2,True), [], [53,107], [52,106]],
                                confirm=False, auction=True)
        original = env.kitty[:]
        env.step({'type':'bid','cards':c('H',2,True)})
        self.assertEqual((env.phase,env.banker,env.current_player), ('final_bidding',0,3))
        self.assertEqual(env.final_bid_queue, [3,2,1])
        self.assertEqual(list(map(len,env.hands)), [25]*4)
        self.assertEqual(env.kitty, original)
        env.step({'type':'bid','cards':[52,106]})
        self.assertEqual((env.phase,env.banker,env.current_player), ('final_bidding',3,2))
        env.step({'type':'bid','cards':[53,107]})
        self.assertEqual((env.phase,env.banker,env.kitty_owner), ('burying',2,2))
        self.assertEqual(env.known_kitties[2][-1],original)
        env.step({'type':'bury','cards':env.hands[2][-8:]})
        self.assertEqual((env.phase,env.current_player),('playing',2))
        env.assert_invariants()

    def test_two_auction_waits_for_three_passes_after_reinforcing(self):
        env = dealt_bidding_env(reserved=[c('H',2,True),[],[],[]],confirm=False,auction=True)
        env.step({'type':'bid','cards':c('H',2,True)})
        for player in [3,2,1]:
            self.assertEqual(env.current_player,player)
            self.assertEqual(len(env.hands[0]),25)
            env.step({'type':'pass'})
        self.assertEqual((env.phase,env.kitty_owner,len(env.hands[0])),('burying',0,33))

    def test_normal_prebottom_counter_then_successive_reburials(self):
        env = dealt_bidding_env(level=6,reserved=[c('H',6),[53,107],[52,106],c('S',6,True)],confirm=False)
        original = env.kitty[:];before=env.hands[0][:];counter_hand=env.hands[3][:]
        env.step({'type':'pass'});env.step({'type':'bid','cards':c('S',6,True)})
        self.assertEqual((env.phase,env.banker,env.kitty_owner),('burying',0,0))
        self.assertEqual(set(env.hands[0])-set(before),set(original))
        self.assertEqual(env.hands[3],counter_hand)
        last = before[:8]
        env.step({'type':'bury','cards':last})
        self.assertEqual(env.counter_queue,[2,1])
        before=env.hands[2][:]
        env.step({'type':'bid','cards':[52,106]})
        self.assertEqual(set(env.hands[2])-set(before),set(last))
        last=before[:8];env.step({'type':'bury','cards':last})
        self.assertEqual(env.counter_queue,[1,0,3])
        before=env.hands[1][:]
        env.step({'type':'bid','cards':[53,107]})
        self.assertEqual(set(env.hands[1])-set(before),set(last))
        env.step({'type':'bury','cards':before[:8]})
        self.assertEqual((env.phase,env.current_player,env.banker),('playing',0,0))
        env.assert_invariants()

    def test_normal_banker_reinforces_then_immediately_buries(self):
        env=dealt_bidding_env(level=6,reserved=[c('H',6,True),[],[],[]],confirm=False)
        env.step({'type':'bid','cards':c('H',6,True)})
        self.assertEqual((env.phase,env.kitty_owner),('burying',0))
        env.step({'type':'bury','cards':env.hands[0][-8:]})
        self.assertEqual(env.counter_queue,[3,2,1])

    def test_normal_dealing_counter_always_leaves_original_bottom_to_banker(self):
        for level in (2,5,6,11,14):
            for counter in (c('S',level,True),[52,106],[53,107]):
                with self.subTest(level=level,counter=counter):
                    prepared=dealt_bidding_env(level=level,
                        reserved=[c('H',level),[],[],counter],confirm=False)
                    env=TractorEnv(GameConfig(level,False,0),0)
                    env.deck=prepared.deck[:];env.kitty=env.deck[100:]
                    original=env.kitty[:]
                    env.hands=[env.deck[:1],[],[],[]]
                    while env.phase=='dealing':
                        p=env.current_player
                        action={'type':'pass'}
                        if p==0 and env.bid is None:
                            action={'type':'bid','cards':c('H',level)}
                        elif p==3 and env.bid and env.bid['strength']==1 and set(counter)<=set(env.hands[3]):
                            action={'type':'bid','cards':counter}
                        env.step(action)
                    while env.phase=='final_bidding':env.step({'type':'pass'})
                    self.assertEqual((env.phase,env.banker,env.kitty_owner,env.current_player),('burying',0,0,0))
                    self.assertEqual(set(env.hands[0])-set(prepared.hands[0]),set(original))
                    self.assertEqual(env.hands[3],prepared.hands[3])
                    self.assertEqual(env.known_kitties[0][-1],original)
                    env.assert_invariants()

    def test_ordinary_diamonds_at_two_require_diamonds_not_trump(self):
        env=fixture([c('D',5),c('C',8),c('S',9),c('D',8)+[53]],level=2,trump='H')
        env.step({'type':'play','cards':c('D',5)})
        self.assertEqual(env.current_player,3)
        self.assertTrue(env.validate_action({'type':'play','cards':c('D',8)})['valid'])
        self.assertEqual(env.validate_action({'type':'play','cards':[53]})['code'],'FOLLOW_SUIT')
        self.assertEqual(list(map(face,env.observe(env.current_player)['forced_play'])),list(map(face,c('D',8))))

    def test_diamond_five_is_level_trump_only_at_five(self):
        env=fixture([c('D',5),c('C',8),c('S',9),c('D',8)+[53]],level=5,trump=None)
        env.step({'type':'play','cards':c('D',5)})
        self.assertEqual(env.observe(env.current_player)['forced_play'],[53])
        self.assertEqual(env.validate_action({'type':'play','cards':c('D',8)})['code'],'FOLLOW_SUIT')
        self.assertTrue(env.validate_action({'type':'play','cards':[53]})['valid'])

    def test_checkpoint_keeps_new_direction_and_auction_queue(self):
        env=dealt_bidding_env(reserved=[c('H',2,True),[],[],[]],confirm=False,auction=True)
        env.step({'type':'bid','cards':c('H',2,True)})
        restored=TractorEnv.from_state(json.loads(json.dumps(env.export_state())))
        self.assertEqual(restored.turn_direction,-1)
        for _ in range(3):
            env.step({'type':'pass'});restored.step({'type':'pass'})
        self.assertEqual(env.export_state(),restored.export_state())
