"""Rule-complete turn-based engine, independent of HTTP and UI.

State transition API: reset, observe, validate_action, step, export_state,
from_state, iter_legal_actions. Illegal actions are atomic: no state mutation.
"""
import copy
import random
from dataclasses import asdict, dataclass
from itertools import combinations
from .cards import MANDATORY, LABELS, card_json, face, rank, suit, points, power, effective_suit, sort_key
from .rules import (IllegalAction, describe, components, validate_play, throw_succeeds,
                    minimal_unit, matched_max, forced_follow)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GameConfig:
    initial_level: int = 2
    auction: bool = True
    banker: int = 0

    def __post_init__(self):
        if type(self.initial_level) is not int or self.initial_level not in range(2, 15):
            raise ValueError("初始等级须为 2 至 A（数字 2..14）")
        if type(self.banker) is not int or self.banker not in range(4):
            raise ValueError("庄家座位须为 0..3")
        if type(self.auction) is not bool:
            raise ValueError("auction 须为布尔值")


@dataclass
class StepResult:
    observation: dict
    rewards: list
    terminated: bool
    truncated: bool
    info: dict


class TractorEnv:
    def __init__(self, config=None, seed=None):
        self.config = config if isinstance(config, GameConfig) else GameConfig(**(config or {}))
        self.reset(seed=seed)

    def reset(self, seed=None):
        self.seed = seed
        self.rng = random.Random(seed)
        self.levels = [self.config.initial_level] * 2
        self.passed = [set(r for r in MANDATORY if r < self.config.initial_level) for _ in range(2)]
        self.banker = self.config.banker
        self.round_no = 0
        self.redeals = 0
        self.revision = 0
        self.events = []
        self.action_log = []
        self.result = None
        self.match_winner = None
        self._start_round(self.config.auction)
        return self.observe(self.current_player)

    def _event(self, kind, **data):
        self.events.append({"seq": len(self.events), "round": self.round_no, "type": kind, **data})

    def _start_round(self, auction=False, redeal=False):
        if not redeal:
            self.round_no += 1
        self.auction = auction
        self.level = self.config.initial_level if auction else self.levels[self.banker % 2]
        self.trump = None
        self.bid = None
        self.bid_history = []
        self.hands = [[], [], [], []]
        self.deck = list(range(108)); self.rng.shuffle(self.deck)
        self.kitty = self.deck[100:]
        self.kitty_public = False
        self.kitty_owner = None
        self.known_kitties = [[], [], [], []]
        self.played = []
        self.trick = []
        self.last_trick = None
        self.trick_no = 0
        self.score = self.captured_score = self.penalty_score = self.bottom_score = 0
        self.deal_count = 0
        self.deal_start = 0 if auction else self.banker
        self.counter_passes = 0
        self.counter_queue = []
        self.final_bid_queue = []
        self.phase = "dealing"
        self._event("round_started", level=self.level, auction=auction, banker=None if auction else self.banker,
                    redeal=redeal)
        self._deal_one()

    def _deal_one(self):
        player = (self.deal_start + self.deal_count) % 4
        self.hands[player].append(self.deck[self.deal_count])
        self.deal_count += 1
        self.current_player = player

    def _finish_dealing(self):
        if self.bid:
            if self.bid["strength"] == 4:
                self._pickup(self.banker)
            else:
                self.phase = "final_bidding"
                self.final_bid_queue = [(self.banker + i) % 4 for i in range(4)]
                self.current_player = self.final_bid_queue[0]
                self._event("final_bidding_started", player=self.current_player)
        elif self.auction:
            self.redeals += 1
            self._event("redeal", reason="抢庄局无人亮主，重新洗牌")
            self._start_round(auction=True, redeal=True)
        else:
            chosen = next((c for c in self.kitty if rank(c) == self.level or rank(c) >= 15), None)
            if chosen is None:
                chosen = max(self.kitty, key=rank)
            self.trump = None if rank(chosen) >= 15 else suit(chosen)
            self.kitty_public = True
            self._event("bottom_revealed", cards=self.kitty[:], trump=self.trump)
            self._begin_play()

    def _pickup(self, player):
        self.final_bid_queue = []
        self.known_kitties[player].append(self.kitty[:])
        self.hands[player].extend(self.kitty)
        self.kitty = []
        self.kitty_owner = player
        self.phase = "burying"
        self.current_player = player
        self._event("kitty_picked_up", player=player)

    def _begin_play(self):
        self.counter_queue = []
        self.phase = "playing"
        self.current_player = self.banker
        self._event("play_started", banker=self.banker, trump=self.trump)

    def _begin_countering(self, owner):
        """Ask the other seats once in order; nobody counters their own bid."""
        self.counter_passes = 0
        self.counter_queue = [(owner + offset) % 4 for offset in range(1, 4)
                              if (owner + offset) % 4 != self.bid["player"]]
        if self.bid["strength"] == 4 or not self.counter_queue:
            self._begin_play()
        else:
            self.phase = "countering"
            self.current_player = self.counter_queue[0]

    def _selected(self, action, player):
        cards = action.get("cards", [])
        if not isinstance(cards, list) or any(type(c) is not int or c not in range(108) for c in cards):
            raise IllegalAction("CARD_ID", "牌须使用 0..107 的实体牌编号列表")
        if len(set(cards)) != len(cards):
            raise IllegalAction("DUPLICATE_CARD", "同一实体牌不能重复出")
        if any(c not in self.hands[player] for c in cards):
            raise IllegalAction("NOT_OWNED", "选择了不在手中的牌")
        return cards

    def _bid_strength(self, cards):
        if len(cards) == 1 and rank(cards[0]) == self.level:
            return 1
        if len(cards) == 2 and face(cards[0]) == face(cards[1]):
            r = rank(cards[0])
            if r == self.level:
                return 2
            if r >= 15:
                return r - 12
        raise IllegalAction("BID_PATTERN", "亮牌须为级牌单张、同花色级牌对或王对")

    def validate_action(self, action, player=None):
        """Observation-safe preflight. Does not inspect other hands for throws."""
        try:
            return self._validate(action, self.current_player if player is None else player)
        except IllegalAction as exc:
            return exc.as_dict()

    def _validate(self, action, player):
        if not isinstance(action, dict):
            raise IllegalAction("ACTION", "动作须为对象")
        if type(player) is not int or player != self.current_player:
            raise IllegalAction("NOT_YOUR_TURN", "还没轮到你")
        kind = action.get("type")
        if self.phase == "match_end":
            raise IllegalAction("MATCH_FINISHED", "比赛已结束，请新建对局")
        if self.phase == "round_end":
            if kind != "next_round":
                raise IllegalAction("NEXT_ROUND", "本局已结束，请开始下一局")
            return {"valid": True, "kind": "next_round", "message": "开始下一局"}
        if self.phase in ("dealing", "final_bidding", "countering"):
            if kind == "pass":
                return {"valid": True, "kind": "pass", "message": "不亮／不反"}
            if kind != "bid":
                raise IllegalAction("BID_OR_PASS", "当前应亮主／反主，或选择不亮")
            if self.phase == "countering" and self.bid and player == self.bid["player"]:
                raise IllegalAction("SELF_COUNTER", "不能反自己当前亮出或反出的主")
            if self.phase == "countering" and player == self.kitty_owner:
                raise IllegalAction("BOTTOM_OWNER_COUNTER", "刚扣底的玩家不能接着反底")
            cards = self._selected(action, player)
            strength = self._bid_strength(cards)
            if not self.bid and strength >= 3:
                raise IllegalAction("FIRST_BID_JOKER", "首次不能用王亮无主，须先亮级牌")
            if self.bid and strength <= self.bid["strength"]:
                raise IllegalAction("BID_TOO_WEAK", "反主须严格高于当前亮主强度")
            return {"valid": True, "kind": "bid", "message": "可以亮牌", "strength": strength}
        cards = self._selected(action, player)
        if self.phase == "burying":
            if kind != "bury" or len(cards) != 8:
                raise IllegalAction("BURY_COUNT", "扣底须恰好选择八张牌")
            return {"valid": True, "kind": "bury", "message": "扣下八张牌"}
        if kind != "play":
            raise IllegalAction("PLAY", "当前应出牌")
        pattern = validate_play(self.hands[player], cards, self.trick[0]["cards"] if self.trick else None,
                                self.level, self.trump)
        return {"valid": True, "kind": pattern["kind"], "message": pattern["label"], "pattern": pattern}

    def step(self, action, player=None):
        player = self.current_player if player is None else player
        validated = self._validate(action, player)  # all checks precede mutation
        before = self.score
        event_start = len(self.events)
        self.action_log.append({"round": self.round_no, "player": player, "action": copy.deepcopy(action)})
        kind = action["type"]
        if self.phase == "round_end":
            self._start_round()
        elif self.phase in ("dealing", "final_bidding", "countering"):
            was_dealing = self.phase == "dealing"
            was_final = self.phase == "final_bidding"
            if kind == "bid":
                cards = action["cards"][:]
                self.bid = {"player": player, "strength": validated["strength"], "cards": cards}
                self.bid_history.append(copy.deepcopy(self.bid))
                self.trump = suit(cards[0]) if rank(cards[0]) < 15 else None
                if (was_dealing or was_final) and self.auction:
                    self.banker = player
                self._event("bid", **self.bid, trump=self.trump, banker=self.banker)
                if not was_dealing:
                    self.counter_passes = 0
                    self.counter_queue = []
                    self._pickup(player)
            if was_dealing:
                self._deal_one() if self.deal_count < 100 else self._finish_dealing()
            elif was_final and kind == "pass":
                self.final_bid_queue.pop(0)
                if self.final_bid_queue:
                    self.current_player = self.final_bid_queue[0]
                else:
                    self._pickup(self.banker)
            elif kind == "pass":
                self.counter_passes += 1
                self.counter_queue.pop(0)
                if not self.counter_queue:
                    self._begin_play()
                else:
                    self.current_player = self.counter_queue[0]
        elif self.phase == "burying":
            self.kitty = action["cards"][:]
            self.hands[player] = [c for c in self.hands[player] if c not in self.kitty]
            self.known_kitties[player].append(self.kitty[:])
            self._event("kitty_buried", player=player)
            self._begin_countering(player)
        else:
            self._play(action["cards"][:], player)
        self.revision += 1
        delta = 0 if kind == "next_round" else self.score - before
        defending = (self.result["banker"] if self.phase in ("round_end", "match_end") else self.banker) % 2
        rewards = [float(-delta if p % 2 == defending else delta) for p in range(4)]
        self.assert_invariants()
        return StepResult(self.observe(self.current_player), rewards, self.phase == "match_end", False,
                          {"round_ended": self.phase in ("round_end", "match_end"),
                           "events": copy.deepcopy(self.events[event_start:]),
                           "result": copy.deepcopy(self.result) if self.phase in ("round_end", "match_end") else None})

    def _play(self, cards, player):
        attempted = cards[:]
        if not self.trick and describe(cards, self.level, self.trump)["kind"] == "throw":
            others = [h for p, h in enumerate(self.hands) if p != player]
            if not throw_succeeds(cards, others, self.level, self.trump):
                penalty = len(cards) * 10
                delta = penalty if player % 2 == self.banker % 2 else -penalty
                self.penalty_score += delta; self.score += delta
                cards = minimal_unit(cards, self.level, self.trump)
                self._event("throw_failed", player=player, attempted=attempted, cards=cards[:], penalty=penalty,
                            score_delta=delta)
        self.hands[player] = [c for c in self.hands[player] if c not in cards]
        self.played.extend(cards)
        self.trick.append({"player": player, "cards": cards[:]})
        self._event("play", player=player, cards=cards[:])
        if len(self.trick) < 4:
            self.current_player = (player + 1) % 4
            return
        winner = self._trick_winner()
        trick_points = sum(points(c) for play in self.trick for c in play["cards"])
        if winner % 2 != self.banker % 2:
            self.captured_score += trick_points; self.score += trick_points
        self.trick_no += 1
        self.last_trick = {"plays": copy.deepcopy(self.trick), "winner": winner, "points": trick_points,
                           "number": self.trick_no}
        self._event("trick_ended", **self.last_trick, score=self.score)
        self.trick = []
        self.current_player = winner
        if not any(self.hands):
            self._settle(winner)

    def _trick_winner(self):
        lead = self.trick[0]["cards"]
        door = effective_suit(lead[0], self.level, self.trump)
        units = components(lead, self.level, self.trump)
        best = (0, matched_max(lead, units, self.level, self.trump))
        winner = self.trick[0]["player"]
        for move in self.trick[1:]:
            cards = move["cards"]
            doors = {effective_suit(c, self.level, self.trump) for c in cards}
            if len(doors) != 1:
                continue
            played_door = next(iter(doors))
            if played_door != door and not (door != "T" and played_door == "T"):
                continue
            strength = matched_max(cards, units, self.level, self.trump)
            if strength is None:
                continue
            value = (1 if door != "T" and played_door == "T" else 0, strength)
            if value > best:
                best = value; winner = move["player"]
        return winner

    def _advance(self, team, amount):
        start = self.levels[team]
        target = min(14, start + amount)
        for r in sorted(MANDATORY):
            if start <= r <= target and r not in self.passed[team]:
                return r
        return target

    def _settle(self, winner):
        banker = self.banker
        defense = banker % 2
        attack = 1 - defense
        winning_cards = next(p["cards"] for p in self.last_trick["plays"] if p["player"] == winner)
        multiplier = 0
        if winner % 2 == attack:
            from collections import Counter
            p = sum(n // 2 for n in Counter(map(face, winning_cards)).values())
            multiplier = 2 ** (p + 1)
            self.bottom_score = sum(points(c) for c in self.kitty) * multiplier
            self.score += self.bottom_score
        old_levels = self.levels[:]
        self.passed[defense].add(self.level)
        defending_won = self.score < 80
        upgrading = defense if defending_won else attack
        amount = (3 if self.score <= 0 else 2 if self.score < 40 else 1) if defending_won else self.score // 40 - 2
        self.levels[upgrading] = self._advance(upgrading, amount)
        downgrade = None
        if winner % 2 == attack and self.level in (11, 14) and any(rank(c) == self.level for c in winning_cards):
            downgrade = 2 if self.level == 11 else 11
            self.levels[defense] = downgrade  # permanent mandatory records are preserved
        if self.level == 14 and defending_won and downgrade is None:
            self.match_winner = defense
        next_banker = (banker + 2) % 4 if defending_won else (banker + 1) % 4
        self.result = {"round": self.round_no, "level": self.level, "banker": banker,
                       "score": self.score, "captured": self.captured_score, "penalty": self.penalty_score,
                       "bottom": self.bottom_score, "multiplier": multiplier,
                       "kitty": self.kitty[:] if self.kitty_public else None,
                       "winner": defense if defending_won else attack, "last_winner": winner,
                       "levels_before": old_levels, "levels_after": self.levels[:], "upgrade": amount,
                       "upgrading_team": upgrading, "downgrade": downgrade, "next_banker": next_banker,
                       "match_winner": self.match_winner}
        self._event("round_ended", **self.result)
        self.phase = "match_end" if self.match_winner is not None else "round_end"
        self.banker = next_banker
        self.current_player = next_banker

    def bid_options(self, player):
        options = []
        from collections import defaultdict
        by_face = defaultdict(list)
        for c in self.hands[player]:
            by_face[face(c)].append(c)
        for cs in by_face.values():
            if rank(cs[0]) == self.level:
                options.append({"type": "bid", "cards": cs[:1]})
            if len(cs) == 2 and (rank(cs[0]) == self.level or rank(cs[0]) >= 15):
                options.append({"type": "bid", "cards": cs[:]})
        return [a for a in options if self.validate_action(a, player)["valid"]]

    def observe(self, player, omniscient=False):
        if type(player) is not int or player not in range(4):
            raise ValueError("观察座位须为 0..3")
        obs = {"schema_version": SCHEMA_VERSION, "revision": self.revision, "round": self.round_no,
               "phase": self.phase, "player": player, "current_player": self.current_player,
               "level": self.level, "level_label": LABELS[self.level], "levels": self.levels[:],
               "passed_levels": [sorted(s) for s in self.passed], "auction": self.auction,
               "banker": None if self.phase == "dealing" and self.auction and not self.bid else self.banker,
               "trump": self.trump, "bid": copy.deepcopy(self.bid), "bid_history": copy.deepcopy(self.bid_history),
               "hand": sorted(self.hands[player], key=lambda c: sort_key(c, self.level, self.trump)),
               "hand_counts": list(map(len, self.hands)), "kitty": self.kitty[:] if self.kitty_public else None,
               "kitty_public": self.kitty_public, "kitty_owner": self.kitty_owner,
               "known_kitties": copy.deepcopy(self.known_kitties[player]), "deal_count": self.deal_count,
               "counter_players": self.counter_queue[:],
               "final_bid_players": self.final_bid_queue[:],
               "drawn_card": self.deck[self.deal_count - 1] if self.phase == "dealing" and player == self.current_player else None,
               "forced_play": self.forced_play(player),
               "redeals": self.redeals, "trick": copy.deepcopy(self.trick), "trick_no": self.trick_no,
               "last_trick": copy.deepcopy(self.last_trick), "score": self.score,
               "captured_score": self.captured_score, "penalty_score": self.penalty_score,
               "bottom_score": self.bottom_score, "result": copy.deepcopy(self.result),
               "match_winner": self.match_winner, "events": copy.deepcopy(self.events[-80:]),
               "bid_options": self.bid_options(player) if player == self.current_player and self.phase in ("dealing", "final_bidding", "countering") else [],
               "action_types": {"dealing": ["bid", "pass"], "final_bidding": ["bid", "pass"], "countering": ["bid", "pass"],
                                "burying": ["bury"], "playing": ["play"], "round_end": ["next_round"],
                                "match_end": []}[self.phase]}
        if omniscient:
            obs["hands"] = [sorted(h, key=lambda c: sort_key(c, self.level, self.trump)) for h in self.hands]
        return obs

    def forced_play(self, player=None):
        """Only the acting follower's sole legal face combination, never a lead."""
        player = self.current_player if player is None else player
        if player != self.current_player or self.phase != "playing" or not self.trick:
            return None
        return forced_follow(self.hands[player], self.trick[0]["cards"], self.level, self.trump)

    def iter_legal_actions(self, player=None):
        """Lazy exhaustive actions. Enumeration may be expensive; never silently caps."""
        player = self.current_player if player is None else player
        if player != self.current_player or self.phase == "match_end":
            return
        if self.phase == "round_end":
            yield {"type": "next_round"}; return
        if self.phase in ("dealing", "final_bidding", "countering"):
            yield {"type": "pass"}
            yield from self.bid_options(player); return
        hand = self.hands[player]
        sizes = [8] if self.phase == "burying" else [len(self.trick[0]["cards"])] if self.trick else range(1, len(hand) + 1)
        kind = "bury" if self.phase == "burying" else "play"
        for n in sizes:
            seen = set()
            for cs in combinations(hand, n):
                faces = tuple(sorted(map(face, cs)))
                if faces in seen:
                    continue
                seen.add(faces)
                action = {"type": kind, "cards": list(cs)}
                if self.validate_action(action, player)["valid"]:
                    yield action

    def export_state(self):
        state = {k: copy.deepcopy(v) for k, v in self.__dict__.items() if k not in ("rng", "config", "passed")}
        state.update(schema_version=SCHEMA_VERSION, config=asdict(self.config),
                     passed=[sorted(p) for p in self.passed], rng_state=self.rng.getstate())
        return state

    @classmethod
    def from_state(cls, state):
        """Trusted simulator checkpoint; contains ALL hidden information."""
        if state.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("不支持的状态版本")
        env = cls.__new__(cls)
        env.config = GameConfig(**state["config"])
        env.__dict__.update(copy.deepcopy({k: v for k, v in state.items() if k not in ("schema_version", "config", "rng_state", "passed")}))
        env.passed = list(map(set, state["passed"]))
        if not hasattr(env, "final_bid_queue"):
            env.final_bid_queue = []
        # Upgrade checkpoints from the earlier four-seat countering procedure.
        env.__dict__.pop("after_bury", None)
        if not hasattr(env, "counter_queue"):
            env.counter_queue = []
            if env.phase == "countering":
                owner = env.kitty_owner
                start = (env.current_player - owner) % 4
                offsets = range(start or 1, 4) if start or env.counter_passes == 0 else ()
                env.counter_queue = [(owner + offset) % 4 for offset in offsets
                                     if (owner + offset) % 4 != env.bid["player"]]
                if env.bid["strength"] == 4 or not env.counter_queue:
                    env._begin_play()
                else:
                    env.current_player = env.counter_queue[0]
        # Older checkpoints included hidden kitty cards in end-of-round results.
        # Only rounds which actually flipped the bottom may publish those cards.
        public_rounds = {e["round"] for e in env.events if e["type"] == "bottom_revealed"}
        for event in env.events:
            if event["type"] == "round_ended" and event["round"] not in public_rounds:
                event["kitty"] = None
        if env.result is not None and env.result["round"] not in public_rounds:
            env.result["kitty"] = None
        def tuples(x):
            return tuple(map(tuples, x)) if isinstance(x, (list, tuple)) else x
        env.rng = random.Random(); env.rng.setstate(tuples(state["rng_state"]))
        env.assert_invariants()
        return env

    def assert_invariants(self):
        cards = [c for h in self.hands for c in h] + self.kitty + self.played
        if self.phase == "dealing":
            cards += self.deck[self.deal_count:100]
        assert len(cards) == 108 and set(cards) == set(range(108)), "实体牌不守恒"
        assert self.score == self.captured_score + self.penalty_score + self.bottom_score
        assert self.current_player in range(4)
        if self.phase == "final_bidding":
            assert self.final_bid_queue and self.current_player == self.final_bid_queue[0]
            assert self.deal_count == 100 and len(self.kitty) == 8
            assert all(len(hand) == 25 for hand in self.hands)
        if self.phase == "countering":
            assert self.counter_queue and self.current_player == self.counter_queue[0]
            assert self.kitty_owner not in self.counter_queue and self.bid["player"] not in self.counter_queue
            assert self.bid["strength"] < 4
        if self.phase in ("round_end", "match_end"):
            assert all(not h for h in self.hands)
