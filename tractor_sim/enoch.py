"""Enoch-inspired playbook port, with honest search under our own table rules.

Adapted from tgoh-ots/shengji-tractor at 21aa754d5128da8f62a7218c00d97715757240d0.
See docs/ai.md and licenses/enoch-MIT.txt. This is a Python adaptation, not the
upstream binary or an assertion of equal playing strength. No model is required.
"""
import copy
import math
import os
import random
import time
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import combinations

from .cards import face, rank, suit, power, points, effective_suit
from .engine import GameConfig, TractorEnv, MANDATORY_COMPLETION_RULE
from .rules import (IllegalAction, blocks, components, describe, follow_witness,
                    matched_max, structure_profile, throw_succeeds, validate_play)

AI_ID = "enoch-table-v1"
AI_NAME = "Enoch 桌规版"


def strength(card, level, trump):
    """Port the upstream semantic strength scale; rules still use cards.power."""
    r = rank(card)
    if r >= 15:
        return 999 if r == 15 else 1000
    if r == level:
        return 998 if suit(card) == trump else 997
    return r


@lru_cache(maxsize=2048)
def score_utility(score):
    """Smooth pressure at promotion bands, with extra weight on taking the table.

    This is a preference, not a claim that 80 is reachable: remaining hand
    points and the sampled bottom are accounted for by the actual rollout.
    """
    bands = sum(8 * math.tanh((score - boundary) / 10)
                for boundary in (0, 40, 80, 120, 160, 200))
    return score + bands + 24 * math.tanh((score - 80) / 10)


def hand_control(hand, level, trump):
    """Remaining control, including the referee's pair/tractor adjacency.

    Point value is deliberately separate: a ten is costly to lose even when
    it is a weak trump, and a level card can be worth spending to save it.
    """
    value = 0.
    for card in hand:
        r = rank(card)
        if effective_suit(card, level, trump) == 'T':
            value += (7.5 if r == 16 else 6.5 if r == 15 else
                      5.5 if r == level and suit(card) == trump else
                      5 if r == level else .8 + .15 * power(card, level, trump))
        else:
            value += 1.2 if r == 14 else .7 if r == 13 else .35 if r == 12 else .05
    counts = Counter(map(face, hand))
    paired = [c for c in hand if counts[face(c)] == 2]
    for unit in components(paired, level, trump):
        tr = effective_suit(unit['faces'][0], level, trump) == 'T'
        value += (2 if tr else 1) * unit['pairs']
        value += (2 if tr else 1.5) * max(0, unit['pairs'] - 1)
    return value


def bid_strength(hand, level, trump):
    """Port bid_strength_enoch: length, control, pair premium and tractors."""
    counts = Counter(map(face, hand))
    trumps = [c for c in hand if effective_suit(c, level, trump) == "T"]
    score = len(trumps) * 1.5
    score += sum(min(20, strength(c, level, trump)) * .3 for c in trumps)
    score += sum(3 if rank(c) == 16 else 2 if rank(c) == 15 else 0 for c in hand)
    score += sum(1.5 if rank(c) == 14 else .7 if rank(c) == 13 else 0
                 for c in hand if effective_suit(c, level, trump) != "T")
    pairs = tuple(sorted(f for f, n in counts.items() if n == 2
                         and effective_suit(f, level, trump) == "T"))
    score += sum(6 if strength(f, level, trump) >= 997 else
                 5 if rank(f) >= 13 else 4 for f in pairs)
    # Our referee's adjacency skips the current level and supports no-trump.
    score += 3 * sum(len(run) == 2 for run in blocks(pairs, level, trump))
    return score


def choose_bury(hand, level, trump):
    """Port choose_kitty_enoch, reducing point budgets for exponential bottoms."""
    by_door = defaultdict(list)
    for c in hand:
        by_door[effective_suit(c, level, trump)].append(c)
    counts = Counter(map(face, hand))
    jokers = sum(rank(c) >= 15 for c in hand)
    nt = len(by_door["T"])
    # Upstream permits 100/25/20/15/10/0. A multi-pair bottom in our rules is
    # much more costly, so even a dominant hand does not bury unlimited points.
    budget = (25 if jokers >= 4 and nt >= 12 else 20 if jokers >= 2 or nt >= 12
              else 15 if jokers >= 1 or nt >= 10 else 10 if nt >= 8 else 5 if nt >= 6 else 0)
    voidable = sorted((len(cs), door) for door, cs in by_door.items()
                      if door != "T" and len(cs) <= 8 and not any(rank(c) == 14 for c in cs))
    voids = set()
    slots, void_points = 8, 0
    for n, door in voidable:
        value = sum(map(points, by_door[door]))
        if n <= slots and void_points + value <= budget:
            voids.add(door); slots -= n; void_points += value

    def priority(c):
        door = effective_suit(c, level, trump)
        s = strength(c, level, trump)
        value = -1000 * (door == "T") - 1000 * (rank(c) == level)
        value -= 1000 * (rank(c) == 14) + 1000 * (s >= 990)
        if counts[face(c)] == 2:
            value -= 5 if door in voids else 1000
        if points(c):
            value += 15 if budget else -40
        value += 15 - min(s, 15)
        if door != "T":
            value += 30 if door in voids else max(0, 6 - len(by_door[door])) * 2
        return (-value, s, c)

    ordered = sorted(hand, key=priority)
    chosen = [c for c in ordered if effective_suit(c, level, trump) in voids]
    value = sum(map(points, chosen))
    for c in ordered:
        if len(chosen) == 8:
            break
        if c not in chosen and value + points(c) <= budget:
            chosen.append(c); value += points(c)
    chosen += [c for c in ordered if c not in chosen][:8 - len(chosen)]
    return chosen


class Knowledge:
    """Only public plays/exposures/voids plus the observing player's own cards."""
    def __init__(self, o):
        self.level, self.trump = o["level"], o["trump"]
        self.voids = [set() for _ in range(4)]
        self.exposed = [set() for _ in range(4)]
        self.played = []
        trick = []
        for event in o.get("public_history", []):
            kind, p = event["type"], event["player"]
            if kind == "bid":
                self.exposed[p].update(event["cards"])
            elif kind == "kitty_buried":
                # A publicly declared card can be buried; its location is no
                # longer known. We must not force it into the bidder's hand.
                self.exposed[p].clear()
            elif kind == "throw_failed":
                self.exposed[p].update(event["attempted"])
            elif kind == "play":
                cs = event["cards"]
                self.played.extend(cs)
                for held in self.exposed:
                    held.difference_update(cs)
                if trick:
                    door = effective_suit(trick[0]["cards"][0], self.level, self.trump)
                    if any(effective_suit(c, self.level, self.trump) != door for c in cs):
                        self.voids[p].add(door)
                trick.append(event)
                if len(trick) == 4:
                    trick = []
        # Complete public ledger is additive to the original observation API.
        self.played = o.get("played_cards", self.played)[:]
        self.known_bottom = (o["kitty"] if o.get("kitty_public") else
                             o["known_kitties"][-1] if o.get("kitty_owner") == o["player"]
                             and o.get("known_kitties") and o["phase"] == "playing" else [])
        seen = set(o["hand"]) | set(self.played) | set(self.known_bottom or [])
        self.unseen = [c for c in range(108) if c not in seen]
        self.unseen_counts = Counter(map(face, self.unseen))

    def safe_unit(self, cards):
        # Hypothetical combined unseen pool: conservative proof, not an actual
        # hidden hand. Includes kitty, so it can underestimate safety.
        return throw_succeeds(cards, [self.unseen], self.level, self.trump)


class _OrderedSample:
    def __init__(self, key):
        self.key = key

    def sample(self, population, k):
        return sorted(population, key=self.key)[:k]


def candidates(o, rng, rollout=False):
    """Bounded proposals, every follow checked against the ORIGINAL hand."""
    hand, level, trump = o["hand"], o["level"], o["trump"]
    lead = o["trick"][0]["cards"] if o["trick"] else None
    if o.get("forced_play"):
        return [o["forced_play"][:]]
    moves, seen = [], set()

    def add(cs):
        key = tuple(sorted(map(face, cs)))
        if not cs or key in seen:
            return
        try:
            validate_play(hand, cs, lead, level, trump)
        except IllegalAction:
            return
        seen.add(key); moves.append(list(cs))

    counts = Counter(map(face, hand))
    if not lead:
        for c in hand:
            add([c])
        pairs = tuple(sorted(f for f, n in counts.items() if n == 2))
        for run in blocks(pairs, level, trump):
            add([c for c in hand if face(c) in run])
        if not rollout:
            k = Knowledge(o)
            by_door = defaultdict(list)
            for c in hand:
                by_door[effective_suit(c, level, trump)].append(c)
            for cs in by_door.values():
                # Only propose a compound if EVERY component is provably safe
                # against the unseen pool. Wrong throws are still legal actions.
                safe = [c for unit in components(cs, level, trump)
                        if k.safe_unit([x for x in cs if face(x) in unit["faces"]])
                        for c in cs if face(c) in unit["faces"]]
                if len(safe) >= 2:
                    add(safe)
        return moves

    n = len(lead)
    door = effective_suit(lead[0], level, trump)
    same = [c for c in hand if effective_suit(c, level, trump) == door]
    pool = same if len(same) >= n else [c for c in hand if c not in same]
    need = n if len(same) >= n else n - len(same)
    prefix = [] if len(same) >= n else same
    if math.comb(len(pool), need) <= (160 if rollout else 1000):
        for cs in combinations(pool, need):
            add(prefix + list(cs))
        return moves
    # Ordered witnesses cover feeding, cheap ducking, high wins and preserving
    # pairs. Capping a witness face discovers alternative legal pair allocations.
    keys = [lambda c: (points(c), strength(c, level, trump), c),
            lambda c: (-points(c), strength(c, level, trump), c),
            lambda c: (-power(c, level, trump), c),
            lambda c: (counts[face(c)] == 2, strength(c, level, trump), points(c), c)]
    for key in keys:
        add(follow_witness(hand, lead, level, trump, _OrderedSample(key)))
    witness = moves[0]
    if len(same) >= n:
        for f in sorted(set(map(face, witness))):
            reduced = hand[:]; reduced.remove(next(c for c in hand if face(c) == f))
            if len(reduced) >= n:
                for key in keys[:2]:
                    add(follow_witness(reduced, lead, level, trump, _OrderedSample(key)))
    if len(same) < n:
        # Explicit structural ruffs; arbitrary sampling rarely produces tractors.
        trumps = [c for c in hand if effective_suit(c, level, trump) == "T"]
        if not same and door != "T" and len(trumps) >= n:
            demands = tuple(u["pairs"] for u in components(lead, level, trump) if u["pairs"])
            _, pf = structure_profile(trumps, demands, level, trump, witness=True)
            required = [c for c in trumps if face(c) in pf]
            for key in keys:
                add(required + sorted([c for c in trumps if c not in required], key=key)[:n-len(required)])
    for _ in range(2 if rollout else 8):
        add(follow_witness(hand, lead, level, trump, rng))
    return moves


def winner_so_far(trick, level, trump):
    """Delegate to the very same comparison implementation as the live referee."""
    referee = TractorEnv.__new__(TractorEnv)
    referee.trick, referee.level, referee.trump = trick, level, trump
    return referee._trick_winner()


def rank_moves(o, moves, knowledge=None):
    """Port the Enoch playbook's lead/follow branches with table-rule comparisons."""
    k = knowledge or Knowledge(o)
    hand, level, trump, me = o["hand"], o["level"], o["trump"], o["player"]
    counts = Counter(map(face, hand))
    nt = sum(effective_suit(c, level, trump) == "T" for c in hand)
    current = winner_so_far(o["trick"], level, trump) if o["trick"] else None
    partner = current is not None and current % 2 == me % 2
    after = [(me + o.get('turn_direction', -1) * i) % 4 for i in range(1, 4 - len(o["trick"]))] if o["trick"] else []
    opp_after = any(p % 2 != me % 2 for p in after)
    pot = sum(points(c) for play in o["trick"] for c in play["cards"])
    attack = me % 2 != o["banker"] % 2

    def locked_winner(trick):
        """Conservative public proof against opponents still to act.

        A high side-suit play alone is not a proof: an opponent may be void
        and ruff, even if that void has not yet been revealed.
        """
        winner = winner_so_far(trick, level, trump)
        enemies = [p for p in after if p % 2 != winner % 2]
        if not enemies:
            return True
        held = next(p['cards'] for p in trick if p['player'] == winner)
        lead = trick[0]['cards']
        units = components(lead,level,trump)
        largest = units[0]['pairs']
        top = matched_max(held,units,level,trump)
        held_door = effective_suit(held[0],level,trump)

        def possible_tops(door):
            pool = [c for c in k.unseen if effective_suit(c,level,trump) == door]
            if len(pool) < len(lead):
                return []
            if not largest:
                return [power(c,level,trump) for c in pool]
            pf = tuple(sorted(f for f,n in Counter(map(face,pool)).items() if n == 2))
            # A beating largest unit is necessary, but not sufficient for a
            # complete match. Overestimating the threat keeps the proof safe.
            return [power(run[-1],level,trump) for run in blocks(pf,level,trump)
                    if len(run) == largest]

        if top is None or any(candidate > top for candidate in possible_tops(held_door)):
            return False
        if held_door == 'T' or not possible_tops('T'):
            return True
        door = effective_suit(lead[0], level, trump)
        return all('T' in k.voids[p] or
                   any(effective_suit(c, level, trump) == door for c in k.exposed[p])
                   for p in enemies)

    locked = locked_winner(o['trick']) if o['trick'] else False

    def retention(c):
        s = strength(c, level, trump)
        return (18 if s >= 999 else 13 if s >= 997 else
                8 if rank(c) == 14 else s * .25) + 4 * (counts[face(c)] == 2)

    def score(cs):
        n = len(cs)
        value = sum(map(points, cs))
        st = max(strength(c, level, trump) for c in cs)
        tr = effective_suit(cs[0], level, trump) == "T"
        remaining = Counter(map(face, cs))
        breaks = sum(counts[f] == 2 and used == 1 for f, used in remaining.items())
        if not o["trick"]:
            safe = k.safe_unit(cs)
            compound = describe(cs, level, trump)["kind"] == "throw"
            result = (n-1)*6 + min(st, 20)*.05 - breaks*5
            if compound and not safe:
                return result - 50*(n-1) - 10*n
            if safe:
                result += 9 + (n-1)*4 + value*.6
            else:
                result -= value*1.2
            if tr and nt < 15 and len(hand) > 8:
                result -= 90 if st >= 999 and n == 1 else 85 if st >= 997 else 30 if st >= 11 else 0
            if not tr:
                if safe and n == 1:
                    result += 4
                if n >= 2:
                    result += ((n-1)*3+3 if safe else (n-1)*2 if n >= 4 else (n-1)*1.5 if st >= 13 else 0)
                    if len(hand) >= 14 and (safe or n >= 4):
                        result += 2
                door = effective_suit(cs[0], level, trump)
                length = sum(effective_suit(c, level, trump) == door for c in hand)
                if safe and length >= 5:
                    result += 1.5 + (length-5)*.4
                opponents = [(me+1)%4, (me+3)%4]
                if door in k.voids[(me+2)%4] and not safe and any(door not in k.voids[p] for p in opponents):
                    result += 2 + 3*max(0, 1-min(st,14)/14)
                result -= sum(door in k.voids[p] and "T" not in k.voids[p] for p in opponents) * (4 + value*.4)
            return result

        would_win = winner_so_far(o["trick"] + [{"player": me, "cards": cs}], level, trump) == me
        door = effective_suit(o["trick"][0]["cards"][0], level, trump)
        threat = any(p % 2 != me % 2 and door in k.voids[p] and "T" not in k.voids[p] for p in after) and door != "T"
        mine_locks = would_win and locked_winner(o['trick'] + [{'player': me, 'cards': cs}])
        max_cost = sum(map(retention, cs))
        point_impact = score_utility(o['score'] + pot + value) - score_utility(o['score'] + pot)
        result = -breaks * 4
        if partner:
            result += point_impact*(3.5 if locked else 1.5)
            if threat:
                result -= point_impact*5
            if would_win and (locked or not mine_locks):
                result -= 15 if opp_after or locked else 12
            # A takeover that adds real security may spend control. An
            # unnecessary takeover pays the same cost as ordinary padding.
            result -= max_cost * (.12 if would_win and not locked else 1)
        elif would_win:
            result += 6 + pot*.8 - min(st,20)*.01
            if door != "T" and {effective_suit(c, level, trump) for c in cs} == {"T"}:
                result += 4 if pot else -3
                if not attack and pot >= 5:
                    result += 5 + pot*.6
                result -= min(st,20)*.03
            result -= max_cost * .08
        else:
            result -= point_impact*4 + max_cost
            if not after:
                result -= value
            if not attack and o["score"] < 80 <= o["score"] + pot + value:
                result -= value*2
        if attack and (would_win or partner and (locked or not opp_after)) and o["score"] < 80 <= o["score"]+pot+value:
            result += 5
        # Last-trick control and special J/A downgrades use the actual winning
        # hand, including a single, pair or a tractor containing the level card.
        if all(size <= n for size in o["hand_counts"]):
            if would_win or partner and not would_win:
                result += 40
            if would_win and attack and level in (11,14) and any(rank(c) == level for c in cs):
                result += 70
        return result

    return sorted(((score(cs), cs) for cs in moves), key=lambda x: (-x[0], tuple(sorted(map(face,x[1])))))


def search_roots(o, ranked, limit):
    """Keep a winning and a ducking option when both exist in a follow.

    Many nearly identical padding moves can otherwise crowd every structural
    ruff out of the bounded search, even when it is needed to save the pot.
    """
    roots = ranked[:limit]
    if o['trick'] and len(roots) >= 2:
        def wins(entry):
            return winner_so_far(o['trick'] + [{'player': o['player'], 'cards': entry[1]}],
                                 o['level'],o['trump']) == o['player']
        category = wins(roots[0])
        if all(wins(entry) == category for entry in roots):
            alternative = next((entry for entry in ranked[limit:] if wins(entry) != category),None)
            if alternative is not None:
                roots[-1] = alternative
    return roots


def sample_world(o, rng, knowledge=None):
    """Sample a SYNTHETIC state; never accepts a live env/export_state.

    Honor physical-card conservation, all public plays, latest own bottom,
    exposed cards still certainly held, public voids and each hand's size.
    Pair/tractor-history inference is intentionally not treated as certainty.
    """
    k = knowledge or Knowledge(o)
    me = o["player"]
    hands = [[] for _ in range(4)]; hands[me] = o["hand"][:]
    bottom = list(k.known_bottom or [])
    allocated = set(hands[me]) | set(k.played) | set(bottom)
    for p in range(4):
        if p != me:
            for c in sorted(k.exposed[p]):
                if c not in allocated:
                    hands[p].append(c); allocated.add(c)
    capacities = [o["hand_counts"][p]-len(hands[p]) for p in range(4)] + [8-len(bottom)]
    if min(capacities) < 0:
        raise ValueError("Inconsistent public card counts")
    cards = [c for c in range(108) if c not in allocated]
    # Randomized augmenting-path matching avoids greedy dead ends when multiple
    # players are void. The bottom has no suit constraints.
    slots = [p for p, n in enumerate(capacities) for _ in range(n)]
    if len(slots) != len(cards):
        raise ValueError("Incomplete public playing ledger")
    rng.shuffle(slots); rng.shuffle(cards)
    eligible = {c: [i for i,p in enumerate(slots) if p == 4 or
                   effective_suit(c,o["level"],o["trump"]) not in k.voids[p]] for c in cards}
    cards.sort(key=lambda c: len(eligible[c]))
    assigned = {}

    def place(c, visited):
        for i in eligible[c]:
            if i in visited:
                continue
            visited.add(i)
            if i not in assigned or place(assigned[i], visited):
                assigned[i] = c
                return True
        return False

    for c in cards:
        if not place(c, set()):
            raise ValueError("Public void/exposure constraints are inconsistent")
    for i,c in assigned.items():
        (bottom if slots[i] == 4 else hands[slots[i]]).append(c)

    env = TractorEnv.__new__(TractorEnv)
    env.config = GameConfig(o["level"], False, o["banker"])
    env.seed = None; env.rng = random.Random(rng.getrandbits(64))
    env.turn_direction = o.get('turn_direction', -1)
    env.levels = o["levels"][:]; env.passed = list(map(set,o["passed_levels"]))
    env.mandatory_completion_rule = MANDATORY_COMPLETION_RULE
    env.banker = o["banker"]; env.round_no = o["round"]; env.redeals = o["redeals"]
    env.revision = o["revision"]; env.auction = o["auction"]
    env.level = o["level"]; env.trump = o["trump"]
    env.bid = copy.deepcopy(o["bid"]); env.bid_history = copy.deepcopy(o["bid_history"])
    env.hands = hands; env.kitty = bottom; env.played = k.played[:]
    env.kitty_public = o["kitty_public"]; env.kitty_owner = o["kitty_owner"]
    env.known_kitties = [[] for _ in range(4)]
    env.known_kitties[me] = copy.deepcopy(o["known_kitties"])
    if env.kitty_owner is not None and env.kitty_owner != me:
        env.known_kitties[env.kitty_owner] = [bottom[:]]
    env.deck = []; env.deal_count = 100; env.deal_start = o["banker"]
    env.counter_queue = []; env.final_bid_queue = []; env.counter_passes = 0
    env.trick = copy.deepcopy(o["trick"]); env.last_trick = copy.deepcopy(o["last_trick"])
    env.trick_no = o["trick_no"]; env.phase = "playing"; env.current_player = me
    env.score = o["score"]; env.captured_score = o["captured_score"]
    env.penalty_score = o["penalty_score"]; env.bottom_score = o["bottom_score"]
    env.result = None; env.match_winner = None; env.action_log = []
    env.public_history = copy.deepcopy(o.get("public_history", []))
    env.events = copy.deepcopy(env.public_history)
    env.assert_invariants()
    return env


def search_value(env, o):
    attack = o["player"] % 2 != o["banker"] % 2
    sign = 1 if attack else -1
    value = sign*(score_utility(env.score)-score_utility(o['score']))
    if env.phase in ("round_end", "match_end"):
        result = env.result
        value += 70 if result["winner"] == o["player"] % 2 else -70
        myteam = o["player"] % 2
        # Actual mandatory-level stops and J/A downgrades come from _settle.
        own_gain = env.levels[myteam]-o["levels"][myteam]
        opp_gain = env.levels[1-myteam]-o["levels"][1-myteam]
        value += 12*(own_gain-opp_gain)
        if env.match_winner is not None:
            value += 400 if env.match_winner == myteam else -400
    else:
        # Strong trumps and structures have different future value. Reduce
        # this value when there are few points left to protect, retaining some
        # end-control value; hidden bottom points come from this sampled world.
        remaining_points = sum(points(c) for hand in env.hands for c in hand)
        weight = .35 + .65 * min(1, (remaining_points + 2*sum(map(points,env.kitty))) / 40)
        if env.level in (11,14):
            weight = 1  # J/A last-trick control still matters without points.
        for p, hand in enumerate(env.hands):
            direction = 1 if p % 2 == o["player"] % 2 else -1
            value += direction * weight * hand_control(hand,env.level,env.trump)
    return value


class EnochPolicy:
    id = AI_ID
    name = AI_NAME
    def __init__(self, seed=None, time_ms=None, worlds=12, root_candidates=6):
        self.rng = random.Random(seed)
        raw = os.environ.get("TRACTOR_AI_TIME_MS", "600") if time_ms is None else time_ms
        self.time_ms = min(3000, max(0, int(raw)))
        self.worlds = min(64, max(1, int(worlds)))
        self.root_candidates = min(12, max(1, int(root_candidates)))
        self.last_search = {}

    def act(self, observation):
        o = observation
        start = time.monotonic()
        # Explicitly reject omniscient inputs to catch integration mistakes.
        if "hands" in o or "deck" in o:
            raise ValueError("EnochPolicy requires a private player observation")
        phase = o["phase"]
        if phase in ("dealing", "final_bidding", "countering"):
            return self._bid(o)
        if phase == "burying":
            return {"type": "bury", "cards": choose_bury(o["hand"],o["level"],o["trump"])}
        if phase == "round_end":
            return {"type": "next_round"}
        if phase != "playing":
            raise ValueError("比赛结束，没有动作")
        k = Knowledge(o)
        ranked = rank_moves(o,candidates(o,self.rng),k)
        selected = ranked[0][1]
        self.last_search = {"worlds": 0, "candidates": len(ranked), "elapsed_ms": 0}
        if self.time_ms and len(ranked) > 1:
            selected = self._search(o,k,ranked,start)
        self.last_search["elapsed_ms"] = round((time.monotonic()-start)*1000,1)
        return {"type": "play", "cards": selected[:]}

    def _bid(self, o):
        options = o["bid_options"]
        if not options:
            return {"type": "pass"}
        rankings = [(bid_strength(o["hand"],o["level"],suit(a["cards"][0]) if rank(a["cards"][0]) < 15 else None),a)
                    for a in options]
        if o.get('auction') and o['level'] == 2 and o['phase'] in ('dealing', 'final_bidding'):
            # Taking the opening bank is mandatory whenever a legal declaration
            # exists. Prefer the strongest declaration, then our best trump suit.
            # Options already exclude self-counters, weak raises and first jokers.
            def auction_priority(item):
                value, action = item
                cards = action['cards']
                tier = 1 if len(cards) == 1 else (rank(cards[0]) - 12 if rank(cards[0]) >= 15 else 2)
                return tier, value
            return copy.deepcopy(max(rankings, key=auction_priority)[1])
        value, action = max(rankings,key=lambda x: (x[0],len(x[1]["cards"])))
        tr = suit(action["cards"][0]) if rank(action["cards"][0]) < 15 else None
        if o["phase"] == "dealing" and len(o["hand"]) < 15:
            pairs = tuple(sorted(f for f,n in Counter(map(face,o["hand"])).items()
                                 if n == 2 and effective_suit(f,o["level"],tr) == "T"))
            if value < 18 or (len(pairs) < 2 and not any(len(b) >= 2 for b in blocks(pairs,o["level"],tr))):
                return {"type": "pass"}
        if o["bid"]:
            old = bid_strength(o["hand"],o["level"],o["trump"])
            # Strengthening our own suit is useful; stealing partner's exchange
            # with an inferior suit is not. The engine alone supplies legal bids.
            same_team = o["bid"]["player"] % 2 == o["player"] % 2
            if tr != o["trump"] and value < old + (6 if same_team else 2):
                return {"type": "pass"}
        return copy.deepcopy(action) if value >= 10 else {"type": "pass"}

    def _search(self, o, k, ranked, start):
        roots = search_roots(o,ranked,self.root_candidates)
        differences = [[] for _ in roots]
        completed = 0
        deadline = start + self.time_ms/1000
        # Same sampled world and same rollout RNG for EVERY root. Only completed
        # batches count, preventing timeouts from favoring earlier candidates.
        for _ in range(self.worlds):
            if time.monotonic() >= deadline:
                break
            world = sample_world(o,self.rng,k)
            batch = []
            rollout_seed = self.rng.getrandbits(64)
            for _, cs in roots:
                if time.monotonic() >= deadline:
                    break
                env = copy.deepcopy(world)
                rng = random.Random(rollout_seed)
                # Full ending when <=8 cards; otherwise a two-trick horizon.
                horizon = 26 if max(o["hand_counts"]) <= 8 else 2
                env.step({"type": "play", "cards": cs})
                while env.phase == "playing" and env.trick_no-o["trick_no"] < horizon:
                    if time.monotonic() >= deadline:
                        break
                    view = env.observe(env.current_player)
                    moves = candidates(view,rng,rollout=True)
                    action = rank_moves(view,moves)[0][1]
                    env.step({"type": "play", "cards": action})
                if env.phase == "playing" and env.trick_no-o["trick_no"] < horizon:
                    break
                batch.append(search_value(env,o))
            if len(batch) != len(roots):
                break
            for i,value in enumerate(batch):
                differences[i].append(value - batch[0])
            completed += 1
        self.last_search["worlds"] = completed
        if not completed:
            return roots[0][1]
        # Compare paired differences in the very same unknown-card worlds.
        # A noisy lucky sample should not erase a sensible hand-written prior.
        means = [sum(ds)/completed for ds in differences]
        errors = [math.sqrt(sum((d-mean)**2 for d in ds)/(completed*(completed-1)))
                  if completed > 1 else 0 for ds,mean in zip(differences,means)]
        penalties = [0 if i == 0 else .5*error + 4/math.sqrt(completed)
                     for i,error in enumerate(errors)]
        scores = [mean + .6*(roots[i][0]-roots[0][0]) - penalties[i]
                  for i,mean in enumerate(means)]
        best = max(range(len(roots)),key=lambda i: scores[i])
        # Aggregate diagnostics only, no sampled hands or alternative private
        # cards. These stay on the policy instance, outside browser snapshots.
        self.last_search.update(selected_index=best, changed_prior=bool(best),
                                paired_delta=round(means[best],3),
                                paired_stderr=round(errors[best],3),
                                confidence_penalty=round(penalties[best],3))
        return roots[best][1]
