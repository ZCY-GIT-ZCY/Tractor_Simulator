"""Replaceable policies receive ONLY the acting player's observation."""
import random
from collections import Counter
from typing import Protocol
from .cards import face, effective_suit
from .rules import blocks, follow_witness


class Policy(Protocol):
    def act(self, observation: dict) -> dict: ...


class RandomPolicy:
    """Random basic leads and a legal structural witness for follows.

    Not uniform over the combinatorial legal action set. No hidden-state access,
    training, heuristic card-value scoring, or truncation of the referee rules.
    """
    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def act(self, observation):
        o = observation
        phase = o["phase"]
        if phase in ("dealing", "final_bidding", "countering"):
            options = o["bid_options"]
            return self.rng.choice(options) if options and self.rng.random() < 0.55 else {"type": "pass"}
        if phase == "burying":
            return {"type": "bury", "cards": self.rng.sample(o["hand"], 8)}
        if phase == "round_end":
            return {"type": "next_round"}
        if phase != "playing":
            raise ValueError("比赛结束，没有动作")
        hand = o["hand"]
        if o["trick"]:
            cs = follow_witness(hand, o["trick"][0]["cards"], o["level"], o["trump"], self.rng)
        else:
            moves = [[c] for c in hand]
            pf = tuple(sorted(f for f, n in Counter(map(face, hand)).items() if n == 2))
            for run in blocks(pf, o["level"], o["trump"]):
                moves.append([c for c in hand if face(c) in run])
            cs = self.rng.choice(moves)
        return {"type": "play", "cards": cs}
