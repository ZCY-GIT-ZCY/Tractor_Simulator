import copy
import random
import threading
import time
import unittest
from unittest.mock import patch

from tractor_sim.engine import GameConfig, TractorEnv
from tractor_sim.enoch import (AI_ID, EnochPolicy, Knowledge, candidates, choose_bury,
                               rank_moves, sample_world, search_roots, search_value,
                               winner_so_far)
from tractor_sim.policy import RandomPolicy
from tractor_sim.rules import validate_play
from tractor_sim.server import Registry
from test_rules import c, fixture


def playing(seed=0, level=2):
    env = TractorEnv(GameConfig(level, False, 0), seed)
    policy = EnochPolicy(seed, time_ms=0)
    while env.phase != "playing":
        env.step(policy.act(env.observe(env.current_player)))
    return env


class EnochRules(unittest.TestCase):
    def test_complete_rounds_all_levels_and_seats(self):
        # Exercise live referee transitions, declarations, reburying, matching,
        # scoring and J/A endings, rather than duplicating policy equations.
        for level in range(2, 15):
            for seed in range(3):
                env = TractorEnv(GameConfig(level, seed % 2 == 0, seed % 4), level*101+seed)
                policies = [EnochPolicy(seed+p, time_ms=0) for p in range(4)]
                for step in range(500):
                    if env.phase in ("round_end", "match_end"):
                        break
                    actor = env.current_player
                    obs = env.observe(actor)
                    action = policies[actor].act(obs)
                    self.assertTrue(env.validate_action(action)["valid"], (level, seed, action))
                    if env.phase == "playing" and step % 9 == 0:
                        world = sample_world(obs,random.Random(step))
                        world.assert_invariants()
                        self.assertEqual(world.hands[actor],obs["hand"])
                    env.step(action)
                else:
                    self.fail(f"round did not end: level={level}, seed={seed}")
                self.assertTrue(env.result)

    def test_no_omniscient_policy_inputs_and_hidden_world_independence(self):
        env = playing(4)
        obs = env.observe(env.current_player)
        swapped = copy.deepcopy(env)
        p = env.current_player
        others = [i for i in range(4) if i != p]
        a,b = others[:2]
        swapped.hands[a][0],swapped.hands[b][0] = swapped.hands[b][0],swapped.hands[a][0]
        self.assertEqual(obs,swapped.observe(p))
        first = EnochPolicy(19,time_ms=0).act(obs)
        self.assertEqual(first,EnochPolicy(19,time_ms=0).act(swapped.observe(p)))
        with self.assertRaises(ValueError):
            EnochPolicy().act(env.observe(p,omniscient=True))

    def test_sampler_respects_public_void_and_latest_private_bottom(self):
        env = playing(2)
        policy = RandomPolicy(10)
        for _ in range(60):
            if env.phase != "playing":
                break
            obs = env.observe(env.current_player)
            world = sample_world(obs,random.Random(91))
            knowledge = Knowledge(obs)
            for p in range(4):
                from tractor_sim.cards import effective_suit
                self.assertFalse(any(effective_suit(card,env.level,env.trump) in knowledge.voids[p]
                                     for card in world.hands[p]))
            if obs["player"] == obs["kitty_owner"]:
                self.assertEqual(set(world.kitty),set(env.kitty))
            env.step(policy.act(obs))

    def test_old_known_bottom_is_not_assumed_current_bottom(self):
        env = playing(7)
        obs = env.observe((env.kitty_owner+1)%4)
        old_cards = [card for card in range(108) if card not in obs["hand"]][:8]
        obs["known_kitties"] = [old_cards]
        self.assertEqual(Knowledge(obs).known_bottom, [])

    def test_public_ledger_survives_event_window_and_checkpoint_upgrade(self):
        env = playing(3)
        policy = RandomPolicy(20)
        while env.phase == "playing":
            env.step(policy.act(env.observe(env.current_player)))
        self.assertEqual(len(env.observe(0)["played_cards"]),100)
        state = env.export_state(); state.pop("public_history")
        restored = TractorEnv.from_state(state)
        self.assertEqual(restored.public_history,env.public_history)

    def test_long_tractor_follow_keeps_subtractor_then_pair(self):
        hand = c("S",3,True)+c("S",4,True)+c("S",12,True)+c("S",13)+c("S",14)
        env = fixture([c("S",8,True)+c("S",9,True)+c("S",10,True),hand,[],[]])
        env.trick = [{"player":0,"cards":env.hands[0][:]}]
        env.current_player = 1
        obs = env.observe(1)
        moves = candidates(obs,random.Random(1))
        self.assertTrue(moves)
        for move in moves:
            validate_play(hand,move,obs["trick"][0]["cards"],6,"D")
            self.assertEqual(set(move),set(c("S",3,True)+c("S",4,True)+c("S",12,True)))

    def test_comparison_uses_largest_unit_not_joker(self):
        trick = [{"player":0,"cards":c("D",8,True)+[53]},
                 {"player":1,"cards":c("D",9,True)+c("D",3)}]
        self.assertEqual(winner_so_far(trick,6,"D"),1)

    def test_feed_partner_and_preserve_high_trump(self):
        hands = [c("S",14), c("S",3), c("S",5)+c("S",4), c("S",8)]
        env = fixture(hands)
        env.trick = [{"player":0,"cards":c("S",14)}, {"player":1,"cards":c("S",3)}]
        env.current_player = 2
        obs = env.observe(2)
        obs["played_cards"] += c("S",14)+c("S",3)
        best = rank_moves(obs,candidates(obs,random.Random(1)))[0][1]
        self.assertEqual(best,c("S",5))

    def test_search_finishes_batches_and_uses_real_referee(self):
        env = playing(8)
        policy = EnochPolicy(1,time_ms=2000,worlds=1,root_candidates=2)
        action = policy.act(env.observe(env.current_player))
        self.assertGreater(policy.last_search["worlds"],0)
        self.assertTrue(env.validate_action(action)["valid"])
        before = env.export_state()
        policy.act(env.observe(env.current_player))
        self.assertEqual(before,env.export_state())

    def test_unwinnable_partner_feed_keeps_level_card_over_ordinary_zero_point_trumps(self):
        # Partner throws a pair plus a single. Our all-trump, pairless hand
        # cannot ruff that structure; feeding the five must not waste the two.
        hands = [c('S',2)+c('H',5)+c('H',12)+c('H',14),
                 c('D',3,True)+c('D',4)+c('D',6),
                 c('S',14,True)+c('S',12)+c('S',3),
                 c('C',7,True)+c('C',8)+c('C',9)]
        env = fixture(hands, level=2, trump='H', banker=2)
        env.step({'type':'play','cards':hands[2][:3]},2)
        env.step({'type':'play','cards':hands[1][:3]},1)
        obs = env.observe(0)
        for budget in (0,600):
            for seed in range(3):
                with self.subTest(budget=budget,seed=seed):
                    move = EnochPolicy(seed,time_ms=budget,worlds=3).act(obs)['cards']
                    validate_play(obs['hand'],move,obs['trick'][0]['cards'],2,'H')
                    self.assertEqual(set(move),set(c('H',5)+c('H',12)+c('H',14)))
                    self.assertNotEqual(winner_so_far(obs['trick']+[{'player':0,'cards':move}],2,'H'),0)

    def test_mandatory_point_follow_does_not_waste_a_level_card_in_its_trump_padding(self):
        for leader, me in ((0,3),(2,0)):
            hands = [c('C',3,True)+c('C',4)+c('C',6),
                     c('D',3,True)+c('D',4)+c('D',6),
                     c('C',7,True)+c('C',8)+c('C',9),
                     c('D',7,True)+c('D',8)+c('D',9)]
            hands[leader] = c('S',14,True)+c('S',12)+c('S',3)
            hands[me] = c('S',5)+c('S',2)+c('H',12)+c('H',14)
            env = fixture(hands,level=2,trump='H',banker=leader)
            env.step({'type':'play','cards':hands[leader][:3]},leader)
            if env.current_player != me:
                p = env.current_player;env.step({'type':'play','cards':env.hands[p][:3]},p)
            obs = env.observe(me)
            for budget in (0,600):
                with self.subTest(leader=leader,budget=budget):
                    move = EnochPolicy(7,time_ms=budget,worlds=3).act(obs)['cards']
                    self.assertEqual(set(move),set(c('S',5)+c('H',12)+c('H',14)))

    def test_zero_point_unwinnable_follow_preserves_level_control(self):
        hands = [c('S',2)+c('H',6)+c('H',12)+c('H',14),
                 c('D',3,True)+c('D',4)+c('D',6),
                 c('S',14,True)+c('S',12)+c('S',3),
                 c('C',7,True)+c('C',8)+c('C',9)]
        env = fixture(hands,level=2,trump='H',banker=2)
        env.step({'type':'play','cards':hands[2][:3]},2)
        env.step({'type':'play','cards':hands[1][:3]},1)
        obs = env.observe(0)
        for budget in (0,600):
            for seed in range(3):
                with self.subTest(budget=budget,seed=seed):
                    move = EnochPolicy(seed,time_ms=budget,worlds=3).act(obs)['cards']
                    self.assertEqual(set(move),set(c('H',6)+c('H',12)+c('H',14)))

    def test_spend_level_to_save_ten_when_enemy_cannot_be_beaten(self):
        # An enemy joker pair cannot be beaten by three unpaired trumps. At
        # 70 attacker points, protecting our ten is worth discarding a two.
        hands = [c('S',2)+c('H',10)+c('H',12)+c('H',14),
                 [53,107,52]+c('C',6),
                 c('S',7,True)+c('S',8)+c('S',9),
                 c('C',7,True)+c('C',8)+c('C',9)]
        env = fixture(hands,level=2,trump='H',banker=0)
        env.score = env.captured_score = 70
        env.current_player = 1  # Enemy won the preceding trick.
        env.step({'type':'play','cards':hands[1][:3]},1)
        obs = env.observe(0)
        for budget in (0,600):
            move = EnochPolicy(2,time_ms=budget,worlds=3).act(obs)['cards']
            self.assertEqual(set(move),set(c('S',2)+c('H',12)+c('H',14)))
            self.assertNotEqual(winner_so_far(obs['trick']+[{'player':0,'cards':move}],2,'H'),0)

    def test_level_can_win_enemy_ace_without_wasting_ordinary_trump(self):
        # The 9999 replay contains this genuine takeover: last to act against
        # an enemy trump ace. Keeping the two would concede the lead.
        hands = [c('C',4)+c('S',4)+c('S',5),
                 c('D',2)+c('C',12)+c('C',14),
                 [105]+c('H',4)+c('H',5),
                 c('C',9)+c('D',4)+c('D',5)]
        env = fixture(hands,level=2,trump='C',banker=0)
        for p in (0,3,2):
            env.step({'type':'play','cards':hands[p][:1]},p)
        obs = env.observe(1)
        best = rank_moves(obs,candidates(obs,random.Random(0)))[0][1]
        self.assertEqual(best,c('D',2))
        self.assertEqual(winner_so_far(obs['trick']+[{'player':1,'cards':best}],2,'C'),1)

    def test_leaf_preserves_level_control_in_zero_point_padding(self):
        hands = [c('S',2)+c('H',6)+c('H',12)+c('H',14),
                 c('D',3,True)+c('D',4)+c('D',6),
                 c('S',14,True)+c('S',12)+c('S',3),
                 c('C',7,True)+c('C',8)+c('C',9)]
        env = fixture(hands,level=2,trump='H',banker=2)
        env.step({'type':'play','cards':hands[2][:3]},2)
        env.step({'type':'play','cards':hands[1][:3]},1)
        obs = env.observe(0)
        keep = copy.deepcopy(env); spend = copy.deepcopy(env)
        keep.step({'type':'play','cards':c('H',6)+c('H',12)+c('H',14)},0)
        spend.step({'type':'play','cards':c('H',6)+c('H',12)+c('S',2)},0)
        for world in (keep,spend):
            world.step({'type':'play','cards':hands[3][:3]},3)
        self.assertEqual(keep.score,spend.score)
        self.assertGreater(search_value(keep,obs),search_value(spend,obs))

    def test_strong_partner_feeds_points_without_stealing_with_level(self):
        hands = [c('C',2)+c('H',3)+c('H',10)+c('H',12),
                 c('H',4)+c('S',4)+c('S',5)+c('S',6),
                 [53,107]+c('H',5)+c('H',6),
                 c('H',7)+c('S',7)+c('S',8)+c('S',9)]
        env = fixture(hands,level=2,trump='H',banker=2)
        env.step({'type':'play','cards':[53]},2)
        env.step({'type':'play','cards':c('H',4)},1)
        obs = env.observe(0)
        for budget in (0,600):
            move = EnochPolicy(2,time_ms=budget,worlds=3).act(obs)['cards']
            self.assertEqual(move,c('H',10))

    def test_search_keeps_structural_ruff_among_many_padding_choices(self):
        hands = [[52,106]+c('C',2,True),
                 c('D',2,True)+c('D',10,True)+c('H',7)+c('D',4)+c('D',8)+c('D',11),
                 c('C',6)+c('C',8)+c('H',5)+c('C',5),
                 c('S',13,True)+c('S',10,True)]
        env = fixture(hands,level=2,trump='D',banker=3)
        env.step({'type':'play','cards':hands[3]},3)
        env.step({'type':'play','cards':hands[2]},2)
        obs = env.observe(1)
        ranked = rank_moves(obs,candidates(obs,random.Random(0)))
        def wins(entry):
            return winner_so_far(obs['trick']+[{'player':1,'cards':entry[1]}],2,'D') == 1
        self.assertFalse(any(wins(entry) for entry in ranked[:2]))
        roots = search_roots(obs,ranked,2)
        self.assertEqual(roots[0],ranked[0])
        self.assertEqual(len(roots),2)
        self.assertTrue(any(wins(entry) for entry in roots))

    def test_known_ruff_risk_changes_feeding_but_partner_security_restores_it(self):
        hands = [c('S',14,True)+c('S',8)+c('S',9),
                 c('H',6)+c('C',6)+c('C',7)+c('C',8),
                 c('H',3)+c('H',4)+c('H',10)+c('C',2),
                 c('S',4,True)+c('C',4)+c('C',5)]
        env = fixture(hands,level=2,trump='H',banker=0)
        env.step({'type':'play','cards':hands[0][:2]},0)
        env.step({'type':'play','cards':hands[3][:2]},3)
        obs = env.observe(2)
        # These are public deductions normally established by earlier follows.
        knowledge = Knowledge(obs);knowledge.voids[1].add('S')
        moves = candidates(obs,random.Random(0))
        self.assertEqual(set(rank_moves(obs,moves,knowledge)[0][1]),set(c('H',3)+c('H',4)))
        knowledge.voids[1].add('T')
        self.assertIn(c('H',10)[0],rank_moves(obs,moves,knowledge)[0][1])

    def test_noisy_search_does_not_override_prior_on_one_lucky_world(self):
        env = playing(8);obs = env.observe(env.current_player)
        policy = EnochPolicy(1,time_ms=3000,worlds=2,root_candidates=2)
        ranked = rank_moves(obs,candidates(obs,random.Random(0)))[:2]
        # Equal priors isolate two paired world advantages (+100, -80).
        # Their positive mean alone used to trigger a speculative override.
        ranked[1] = (ranked[0][0],ranked[1][1])
        with patch('tractor_sim.enoch.search_value',side_effect=[0,100,0,-80]):
            selected = policy._search(obs,Knowledge(obs),ranked,time.monotonic())
        self.assertEqual(policy.last_search['worlds'],2)
        self.assertEqual(selected,ranked[0][1])

    def test_consistent_search_advantage_can_still_override_prior(self):
        env = playing(8);obs = env.observe(env.current_player)
        policy = EnochPolicy(1,time_ms=3000,worlds=2,root_candidates=2)
        ranked = rank_moves(obs,candidates(obs,random.Random(0)))[:2]
        ranked[1] = (ranked[0][0],ranked[1][1])
        with patch('tractor_sim.enoch.search_value',side_effect=[0,30,0,30]):
            selected = policy._search(obs,Knowledge(obs),ranked,time.monotonic())
        self.assertEqual(policy.last_search['worlds'],2)
        self.assertEqual(selected,ranked[1][1])
        self.assertTrue(policy.last_search['changed_prior'])


class AsyncPolicies(unittest.TestCase):
    def test_slow_policy_does_not_lock_room_and_stale_decision_is_discarded(self):
        entered, release = threading.Event(),threading.Event()
        class SlowPolicy:
            def __init__(self, seed):
                pass
            def act(self, obs):
                self_seen.append(obs)
                entered.set(); release.wait(3)
                return {"type":"pass"}
        self_seen = []
        registry = Registry(policy_factory=SlowPolicy)
        # After AI seat 0 draws, counterclockwise rotation reaches human 3.
        session = registry.enter({"mode":"pve","seat":3})
        room = registry.lookup(session["token"])
        room.next_bot_at = 0
        thread = threading.Thread(target=registry.run_bots)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(room.condition.acquire(timeout=.1))
            try:
                snapshot = room.snapshot(session["token"])
                self.assertTrue(snapshot["bot_thinking"])
                # Simulates a concurrent accepted action while a policy was
                # planning (possible for a human seat under autoplay).
                room.env.step({"type":"pass"})
                revision = room.env.revision
                room.bump()
            finally:
                room.condition.release()
            release.set()
            deadline = time.monotonic()+2
            while room.pending is not None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertEqual(room.env.revision,revision)
            self.assertNotIn("hands",self_seen[0])
            self.assertNotIn("deck",self_seen[0])
        finally:
            release.set(); registry.stopped.set(); thread.join(4)

    def test_default_pve_has_three_separate_enoch_instances(self):
        registry = Registry()
        s = registry.enter({"mode":"pve","seat":2})
        room = registry.lookup(s["token"])
        self.assertEqual(s["ai"]["id"],AI_ID)
        self.assertEqual(sum(p["bot"] for p in s["players"]),3)
        self.assertEqual(len({id(p) for p in room.policies}),4)
        self.assertTrue(all(isinstance(p,EnochPolicy) for p in room.policies))
        self.assertNotIn("hands",s["state"])
        self.assertIsNone(s["state"]["kitty"])

    def test_custom_policy_metadata_and_string_seed(self):
        registry = Registry(policy_factory=RandomPolicy)
        session = registry.enter({"mode":"pve","seed":"text-seed"})
        self.assertEqual(session["ai"]["id"],"custom")
        self.assertTrue(all(isinstance(p,RandomPolicy) for p in
                            registry.lookup(session["token"]).policies))


if __name__ == "__main__":
    unittest.main()
