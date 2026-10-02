"""Exact follow obligations; throw success is intentionally absent from validation.

The engine's CPU implementation is the reference for any future batched backend.
Pair matching searches all equivalent allocations instead of greedily choosing a
physical pair which can make a later component impossible.
"""
from collections import Counter
from functools import lru_cache
from itertools import product
import random
from .cards import face, effective_suit, power, rank, card_json


class IllegalAction(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message

    def as_dict(self):
        return {"valid": False, "code": self.code, "message": self.message}


@lru_cache(maxsize=4096)
def blocks(pair_faces, level, trump):
    """Every same-door consecutive run of physical-face pairs, including singles."""
    grouped = {}
    for f in pair_faces:
        grouped.setdefault(effective_suit(f, level, trump), {}).setdefault(power(f, level, trump), []).append(f)
    result = []
    for door, by_power in grouped.items():
        for start in sorted(by_power):
            end = start
            while end in by_power:
                for choices in product(*(by_power[k] for k in range(start, end + 1))):
                    result.append(tuple(choices))
                end += 1
    return tuple(result)


def components(cards, level, trump):
    """Canonical longest-run-first decomposition; physical ids kept for display."""
    c = Counter(map(face, cards))
    pairs = {f for f, n in c.items() if n == 2}
    units = []
    while pairs:
        choices = blocks(tuple(sorted(pairs)), level, trump)
        chosen = min(choices, key=lambda b: (-len(b), power(b[0], level, trump), b))
        units.append({"pairs": len(chosen), "faces": list(chosen),
                      "power": max(power(f, level, trump) for f in chosen)})
        pairs.difference_update(chosen)
    for f, n in sorted(c.items(), key=lambda x: (power(x[0], level, trump), x[0])):
        if n == 1:
            units.append({"pairs": 0, "faces": [f], "power": power(f, level, trump)})
    return sorted(units, key=lambda u: (-u["pairs"], -u["power"], u["faces"]))


def describe(cards, level, trump):
    if not cards:
        return {"kind": "empty", "label": "请选择牌", "units": []}
    doors = {effective_suit(c, level, trump) for c in cards}
    if len(doors) != 1:
        return {"kind": "mixed", "label": "多门混牌", "units": []}
    units = components(cards, level, trump)
    if len(cards) == 1:
        kind, label = "single", "单张"
    elif len(units) == 1 and units[0]["pairs"] == 1:
        kind, label = "pair", "对子"
    elif len(units) == 1 and units[0]["pairs"] >= 2:
        kind, label = "tractor", f'{units[0]["pairs"]} 连拖'
    else:
        kind, label = "throw", "甩牌尝试"
    return {"kind": kind, "label": label, "door": next(iter(doors)), "units": units, "count": len(cards)}


def structure_profile(cards, demands, level, trump, witness=False):
    """Lexicographically best ordered run lengths, with a matching pair witness.

    For a 3-pair demand: [3,0,0] > [2,1,0] > [1,1,1]. Every
    allocation of equal best length is explored for later obligations.
    """
    pf = tuple(sorted(f for f, n in Counter(map(face, cards)).items() if n >= 2))
    available = (1 << len(pf)) - 1
    index = {f: i for i, f in enumerate(pf)}
    runs = tuple((sum(1 << index[f] for f in b), len(b), b)
                 for b in blocks(pf, level, trump))

    @lru_cache(None)
    def partition(mask, slots):
        if not slots or not mask:
            return (((), mask, ()),)
        choices = [b for b in runs if b[1] <= slots and b[0] & mask == b[0]]
        size = max(b[1] for b in choices)
        outputs = {}
        for bm, length, faces in choices:
            if length != size:
                continue
            for profile, remaining, used in partition(mask ^ bm, slots - size):
                candidate = ((length,) + profile, remaining, faces + used)
                if remaining not in outputs or candidate[0] > outputs[remaining][0]:
                    outputs[remaining] = candidate
        return tuple(outputs.values())

    @lru_cache(None)
    def solve(mask, i):
        if i == len(demands):
            return (), ()
        slots = demands[i]
        best = None
        for prof, remaining, used in partition(mask, slots):
            tail, later = solve(remaining, i + 1)
            candidate = (prof + (0,) * (slots - len(prof)) + tail, used + later)
            if best is None or candidate[0] > best[0]:
                best = candidate
        return best

    result = solve(available, 0)
    return result if witness else result[0]


def matched_max(cards, units, level, trump):
    """Full match; compare ONLY the largest pattern's maximum card.

    A pair outranks a single as the comparison pattern; a longer tractor
    outranks a shorter one. All remaining components must still match.
    """
    c = Counter(map(face, cards))
    demands = tuple(u["pairs"] for u in units)
    all_faces = tuple(sorted(c))

    @lru_cache(None)
    def match(amounts, i):
        if i == len(demands):
            return () if not any(amounts) else None
        # Once only singles remain, any allocation works. Avoid enumerating
        # permutations of the same remaining cards in large all-single throws.
        if not any(demands[i:]):
            remaining = [power(f, level, trump) for f, n in zip(all_faces, amounts) for _ in range(n)]
            return tuple(sorted(remaining, reverse=True)) if len(remaining) == len(demands) - i else None
        need = demands[i]
        if not need:
            results = []
            for j, n in enumerate(amounts):
                if n:
                    nxt = list(amounts); nxt[j] -= 1
                    tail = match(tuple(nxt), i + 1)
                    if tail is not None:
                        results.append((power(all_faces[j], level, trump),) + tail)
            return max(results) if results else None
        pairs = tuple(f for f, n in zip(all_faces, amounts) if n >= 2)
        results = []
        for run in blocks(pairs, level, trump):
            if len(run) != need:
                continue
            nxt = list(amounts)
            for f in run:
                nxt[all_faces.index(f)] -= 2
            tail = match(tuple(nxt), i + 1)
            if tail is not None:
                results.append((power(run[-1], level, trump),) + tail)
        return max(results) if results else None

    result = match(tuple(c[f] for f in all_faces), 0)
    return result[0] if result is not None else None


def validate_play(hand, selected, lead, level, trump):
    if not selected:
        raise IllegalAction("EMPTY", "请选择至少一张牌")
    if lead is None:
        pattern = describe(selected, level, trump)
        if pattern["kind"] == "mixed":
            raise IllegalAction("MIXED_LEAD", "领出只能选择同一门的牌，不能混门")
        return pattern
    n = len(lead)
    if len(selected) != n:
        raise IllegalAction("CARD_COUNT", f"本轮须跟 {n} 张牌")
    door = effective_suit(lead[0], level, trump)
    held = [c for c in hand if effective_suit(c, level, trump) == door]
    followed = [c for c in selected if effective_suit(c, level, trump) == door]
    if len(followed) != min(n, len(held)):
        raise IllegalAction("FOLLOW_SUIT", "必须先跟首家那一门；不足时先跟完同门牌")
    units = components(lead, level, trump)
    demands = tuple(u["pairs"] for u in units if u["pairs"])
    if demands and structure_profile(followed, demands, level, trump) != structure_profile(held, demands, level, trump):
        raise IllegalAction("FOLLOW_STRUCTURE", "须优先跟最长拖拉机，再跟对子，最后补单张")
    pattern = describe(selected, level, trump)
    pattern["label"] = f"跟 {n} 张"
    return pattern


def throw_succeeds(selected, others, level, trump):
    """Privileged referee operation: NEVER call for pre-submit action masks."""
    door = effective_suit(selected[0], level, trump)
    for u in components(selected, level, trump):
        for hand in others:
            same = [c for c in hand if effective_suit(c, level, trump) == door]
            if not u["pairs"]:
                if any(power(c, level, trump) > u["power"] for c in same):
                    return False
            else:
                pf = tuple(sorted(f for f, n in Counter(map(face, same)).items() if n == 2))
                if any(len(b) == u["pairs"] and power(b[-1], level, trump) > u["power"]
                       for b in blocks(pf, level, trump)):
                    return False
    return True


def minimal_unit(selected, level, trump):
    smallest = min(map(face, selected), key=lambda f: (power(f, level, trump), f))
    return [c for c in selected if face(c) == smallest]


def follow_witness(hand, lead, level, trump, rng):
    """Construct a legal follow without enumerating combinatorially many actions."""
    door = effective_suit(lead[0], level, trump)
    same = [c for c in hand if effective_suit(c, level, trump) == door]
    n = len(lead)
    if len(same) < n:
        return same + rng.sample([c for c in hand if c not in same], n - len(same))
    units = components(lead, level, trump)
    demands = tuple(u["pairs"] for u in units if u["pairs"])
    _, pf = structure_profile(same, demands, level, trump, witness=True)
    selected = [c for c in same if face(c) in pf]
    selected += rng.sample([c for c in same if c not in selected], n - len(selected))
    return selected


def forced_follow(hand, lead, level, trump):
    """Return the sole legal face combination, or None when there is a choice.

    An alternative must use fewer copies of at least one witness face. For each
    such face, cap its availability one below the witness and construct the best
    remaining follow. Validation against the ORIGINAL hand preserves its suit
    and structure obligations. This avoids enumerating all n-card subsets.
    """
    result = _forced_follow(tuple(hand), tuple(lead), level, trump)
    return list(result) if result is not None else None


@lru_cache(maxsize=4096)
def _forced_follow(hand, lead, level, trump):
    if not lead or len(hand) < len(lead):
        return None
    witness = follow_witness(hand, lead, level, trump, random.Random(0))
    for f, count in Counter(map(face, witness)).items():
        remaining = []; kept = 0
        for card in hand:
            if face(card) == f:
                if kept == count - 1:
                    continue
                kept += 1
            remaining.append(card)
        if len(remaining) < len(lead):
            continue
        alternative = follow_witness(remaining, lead, level, trump, random.Random(0))
        try:
            validate_play(hand, alternative, lead, level, trump)
        except IllegalAction:
            continue
        return None
    return tuple(witness)
