"""Physical card ids 0..107; face ids 0..53. No model representation implied."""
from collections import Counter

SUITS = ("S", "H", "D", "C")
SYMBOLS = ("♠", "♥", "♦", "♣")
RANKS = tuple(range(2, 15))
LABELS = {**{r: str(r) for r in range(2, 11)}, 11: "J", 12: "Q", 13: "K", 14: "A"}
MANDATORY = frozenset((2, 5, 10, 11, 13, 14))


def face(card):
    return card % 54


def rank(card):
    f = face(card)
    return f % 13 + 2 if f < 52 else 15 + f - 52


def suit(card):
    return SUITS[face(card) // 13] if face(card) < 52 else None


def points(card):
    return 5 if rank(card) == 5 else 10 if rank(card) in (10, 13) else 0


def effective_suit(card, level, trump):
    return "T" if rank(card) >= 15 or rank(card) == level or suit(card) == trump else suit(card)


def power(card, level, trump):
    r = rank(card)
    if effective_suit(card, level, trump) != "T":
        return r - 2 - (r > level)
    if trump is None:
        return 0 if r == level else r - 14
    if r >= 15:
        return r - 1  # small 14, big 15
    if r == level:
        return 13 if suit(card) == trump else 12
    return r - 2 - (r > level)


def sort_key(card, level, trump):
    s = effective_suit(card, level, trump)
    return (s == "T", SUITS.index(s) if s != "T" else 4, power(card, level, trump), face(card), card)


def card_json(card):
    f = face(card)
    return {"id": card, "face": f, "suit": suit(card), "rank": rank(card),
            "label": LABELS.get(rank(card), "小王" if f == 52 else "大王"),
            "symbol": SYMBOLS[f // 13] if f < 52 else "★", "points": points(card)}


def counts(cards):
    return Counter(map(face, cards))
