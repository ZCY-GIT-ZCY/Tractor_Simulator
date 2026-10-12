import copy
import random
import threading
import time
import unittest

from tractor_sim.engine import GameConfig, TractorEnv
from tractor_sim.enoch import (AI_ID, EnochPolicy, Knowledge, candidates, choose_bury,
                               rank_moves, sample_world, winner_so_far)
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
