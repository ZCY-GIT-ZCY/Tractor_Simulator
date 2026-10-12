"""Independent table-rule audit and balanced search-policy strength evaluation.

The oracle below does not import tractor_sim.rules or cards. It is an offline
auditor: privileged snapshots never enter a policy. Reports redact burial cards;
private failure snapshots are saved only in local build/.
"""
import argparse
import json
import random
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
from itertools import combinations
from pathlib import Path

from tractor_sim import EnochPolicy, GameConfig, RandomPolicy, TractorEnv

MANDATORY = {2,5,10,11,13,14}


def rank(c):
    f = c % 54
    return f % 13+2 if f < 52 else f-37


def door(c, level, trump):
    if rank(c) >= 15 or rank(c) == level:
        return "T"
    s = "SHDC"[(c%54)//13]
    return "T" if s == trump else s


def value(c, level, trump):
    r = rank(c)
    if door(c,level,trump) != "T":
        return [v for v in range(2,15) if v != level].index(r)
    if trump is None:
        return 0 if r == level else r-14
    if r >= 15:
        return r-1
    if r == level:
        return 13 if "SHDC"[(c%54)//13] == trump else 12
    return [v for v in range(2,15) if v != level].index(r)


def point(c):
    return 5 if rank(c) == 5 else 10 if rank(c) in (10,13) else 0


@lru_cache(maxsize=8192)
def runs(pairs, level, trump):
    """Exhaust subsets and check adjacent powers, rather than engine blocks()."""
    result = []
    for n in range(1,len(pairs)+1):
        for part in combinations(pairs,n):
            if len({door(f,level,trump) for f in part}) != 1:
                continue
            ordered = tuple(sorted(part,key=lambda f:(value(f,level,trump),f)))
            powers = [value(f,level,trump) for f in ordered]
            if all(b == a+1 for a,b in zip(powers,powers[1:])):
                result.append(ordered)
    return tuple(result)


def units(cards, level, trump):
    counts = Counter(c%54 for c in cards)
    pairs = set(f for f,n in counts.items() if n == 2)
    parts = []
    while pairs:
        run = min(runs(tuple(sorted(pairs)),level,trump),
                  key=lambda x:(-len(x),value(x[0],level,trump),x))
        parts.append((len(run),run,value(run[-1],level,trump)))
        pairs -= set(run)
    parts += [(0,(f,),value(f,level,trump)) for f,n in counts.items() if n == 1]
    return sorted(parts,key=lambda u:(-u[0],-u[2],u[1]))


def profile(cards, demands, level, trump):
    """Enumerate consumed pair subsets; optimize each subset's entire cover."""
    pairs = frozenset(f for f,n in Counter(c%54 for c in cards).items() if n == 2)

    @lru_cache(None)
    def cover(available):
        if not available:
            return ()
        return max((len(run),)+cover(available-frozenset(run))
                   for run in runs(tuple(sorted(available)),level,trump))

    @lru_cache(None)
    def solve(available, i):
        if i == len(demands):
            return ()
        n = min(len(available),demands[i])
        outcomes = []
        for subset in combinations(sorted(available),n):
            subset = frozenset(subset)
            p = cover(subset)
            outcomes.append(p+(0,)*(demands[i]-len(p))+solve(available-subset,i+1))
        return max(outcomes)
    return solve(pairs,0)


def match_top(cards, pattern, level, trump):
    counts = Counter(c%54 for c in cards)
    demands = tuple(u[0] for u in pattern)

    @lru_cache(None)
    def match(entries, i):
        cs = dict(entries)
        if i == len(demands):
            return () if not cs else None
        if not demands[i]:
            # All single units occur at the end of our canonical decomposition.
            remaining = [value(f,level,trump) for f,n in cs.items() for _ in range(n)]
            return tuple(sorted(remaining,reverse=True)) if len(remaining) == len(demands)-i else None
        outcomes = []
        available = tuple(sorted(f for f,n in cs.items() if n == 2))
        for run in runs(available,level,trump):
            if len(run) != demands[i]:
                continue
            rest = cs.copy()
            for f in run:
                rest.pop(f)
            tail = match(tuple(sorted(rest.items())),i+1)
            if tail is not None:
                outcomes.append((value(run[-1],level,trump),)+tail)
        return max(outcomes) if outcomes else None
    result = match(tuple(sorted(counts.items())),0)
    return result[0] if result is not None else None


def winner(trick, level, trump):
    lead = trick[0]["cards"]
    led = door(lead[0],level,trump)
    pattern = units(lead,level,trump)
    best, seat = (0,match_top(lead,pattern,level,trump)),trick[0]["player"]
    for play in trick[1:]:
        cs = play["cards"]
        doors = {door(c,level,trump) for c in cs}
        if len(doors) != 1:
            continue
        d = next(iter(doors))
        if d != led and not (led != "T" and d == "T"):
            continue
        top = match_top(cs,pattern,level,trump)
        if top is None:
            continue
        compared = (int(d == "T" and led != "T"),top)
        if compared > best:
            best,seat = compared,play["player"]
    return seat


def check_play(hand, selected, trick, level, trump):
    assert selected and len(set(selected)) == len(selected)
    assert set(selected) <= set(hand), "card ownership"
    if not trick:
        assert len({door(c,level,trump) for c in selected}) == 1, "mixed lead"
        return
    lead = trick[0]["cards"]
    assert len(selected) == len(lead), "follow length"
    d = door(lead[0],level,trump)
    held = [c for c in hand if door(c,level,trump) == d]
    followed = [c for c in selected if door(c,level,trump) == d]
    assert len(followed) == min(len(lead),len(held)), "follow suit"
    demands = tuple(u[0] for u in units(lead,level,trump) if u[0])
    assert profile(followed,demands,level,trump) == profile(held,demands,level,trump), "follow structure"


def throw_ok(cards, hands, level, trump):
    d = door(cards[0],level,trump)
    for n,fs,top in units(cards,level,trump):
        for hand in hands:
            same = [c for c in hand if door(c,level,trump) == d]
            if not n:
                if any(value(c,level,trump) > top for c in same):
                    return False
            else:
                pairs = tuple(sorted(f for f,count in Counter(c%54 for c in same).items() if count == 2))
                if any(len(run) == n and value(run[-1],level,trump) > top
                       for run in runs(pairs,level,trump)):
                    return False
    return True


def audit_transition(before, action, env, metrics):
    phase, p = before["phase"],before["current_player"]
    cs = action.get("cards",[])
    level, trump = before["level"],before["trump"]
    metrics["actions"] += 1
    all_cards = sum(env.hands,[])+env.kitty+env.played
    if env.phase == "dealing":
        all_cards += env.deck[env.deal_count:100]
    assert len(all_cards) == 108 and set(all_cards) == set(range(108)), "108-card conservation"
    if phase in ("dealing","final_bidding","countering"):
        if action["type"] == "bid":
            assert set(cs) <= set(before["hands"][p])
            strength = (1 if len(cs) == 1 and rank(cs[0]) == level else
                        2 if len(cs) == 2 and cs[0]%54 == cs[1]%54 and rank(cs[0]) == level else
                        rank(cs[0])-12 if len(cs) == 2 and cs[0]%54 == cs[1]%54 and rank(cs[0]) >= 15 else 0)
            assert strength and (before["bid"] is not None or strength < 3)
            assert before["bid"] is None or strength > before["bid"]["strength"]
            if before['bid'] and before['bid']['player'] == p:
                assert before['bid']['strength'] == 1 and strength == 2
                assert cs[0]%54 == before['bid']['cards'][0]%54, 'self counter instead of same-suit reinforcement'
            if phase == 'dealing':
                assert env.deal_count == before['deal_count'] and env.current_player == p
                assert env.hands == before['hands'], 'dealing bid must not draw'
            if phase == "countering":
                assert p not in (before["kitty_owner"],before["bid"]["player"]), "counter own bottom/bid"
            elif before["auction"] and (before['bid'] is None or level == 2):
                assert env.banker == p, "auction bidder must become banker"
            if phase != "dealing":
                if phase == 'final_bidding' and before['auction'] and level == 2 and strength < 4:
                    assert env.phase == 'final_bidding' and env.final_bid_queue == [(p-i)%4 for i in range(1,4)]
                    assert env.hands == before['hands'] and env.kitty == before['kitty']
                else:
                    owner = before['banker'] if phase=='final_bidding' and not before['auction'] else p
                    assert set(env.hands[owner]) == set(before["hands"][owner]+before["kitty"]), "bottom pickup"
                    assert env.kitty == [] and env.kitty_owner == owner
            metrics["bids"] += 1
        if not before["auction"] or phase == "countering":
            assert env.banker == before["banker"], "banker changed illegally"
        if phase == "dealing" and before["deal_count"] == 100 and action["type"] == "pass" and before["bid"] is None:
            if before["auction"]:
                assert env.redeals == before["redeals"]+1 and env.phase == "dealing" and env.deal_count == 1
            else:
                kitty = before["kitty"]
                chosen = next((c for c in kitty if rank(c) == level or rank(c) >= 15),None)
                if chosen is None:chosen = max(kitty,key=rank)
                expected_trump = None if rank(chosen) >= 15 else "SHDC"[(chosen%54)//13]
                assert env.trump == expected_trump and env.kitty_public
                assert env.kitty == kitty and env.kitty_owner is None
                assert env.phase == "playing" and env.current_player == before["banker"]
    elif phase == "burying":
        assert len(cs) == 8 and len(set(cs)) == 8 and set(cs) <= set(before["hands"][p])
        assert env.kitty == cs and len(env.hands[p]) == 25, "buried bottom"
        bidder = before["bid"]["player"] if before["bid"] else None
        expected = [(p-i)%4 for i in range(1,4) if (p-i)%4 != bidder]
        if (before["bid"] and before["bid"]["strength"] == 4) or not expected:
            assert env.phase == "playing" and env.current_player == before["banker"]
        else:
            assert env.counter_queue == expected and env.current_player == expected[0]
        metrics["burials"] += 1
    elif phase == "playing":
        check_play(before["hands"][p],cs,before["trick"],level,trump)
        actual = cs[:]
        expected_penalty = 0
        if not before["trick"] and len(units(cs,level,trump)) > 1:
            if not throw_ok(cs,[h for seat,h in enumerate(before["hands"]) if seat != p],level,trump):
                smallest = min((c%54 for c in cs),key=lambda f:(value(f,level,trump),f))
                actual = [c for c in cs if c%54 == smallest]
                expected_penalty = 10*len(cs)*(1 if p%2 == before["banker"]%2 else -1)
                metrics["failed_throws"] += 1
        assert set(env.hands[p]) == set(before["hands"][p])-set(actual), "actual throw cards"
        assert env.penalty_score == before["penalty_score"]+expected_penalty, "10n throw penalty"
        trick = before["trick"]+[{"player":p,"cards":actual}]
        if len(trick) == 4:
            w = winner(trick,level,trump)
            assert env.last_trick["winner"] == w, "largest-pattern winner"
            pot = sum(point(c) for play in trick for c in play["cards"])
            assert env.captured_score == before["captured_score"]+(pot if w%2 != before["banker"]%2 else 0)
            if env.phase == "playing":
                assert env.current_player == w, "next lead owner"
            else:
                winning = next(play["cards"] for play in trick if play["player"] == w)
                pairs = sum(n//2 for n in Counter(c%54 for c in trick[0]['cards']).values())
                multiplier = 2**(pairs+1) if w%2 != before["banker"]%2 else 0
                assert env.result["multiplier"] == multiplier
                assert env.bottom_score == sum(point(c) for c in env.kitty)*multiplier, "bottom multiplier"
                defend = before["banker"]%2
                team = defend if env.score < 80 else 1-defend
                amount = (3 if env.score <= 0 else 2 if env.score < 40 else 1) if env.score < 80 else env.score//40-2
                passed = [set(s) for s in before["passed"]]; passed[defend].add(level)
                assert env.passed == passed, "permanent defended-level records"
                levels = before["levels"][:]
                target = min(14,levels[team]+amount)
                stop = [r for r in sorted(MANDATORY) if levels[team] <= r <= target and r not in passed[team]]
                levels[team] = stop[0] if stop else target
                downgrade = None
                if w%2 != defend and level in (11,14) and any(rank(c) == level for c in winning):
                    downgrade = 2 if level == 11 else 11; levels[defend] = downgrade
                assert env.levels == levels and env.result["downgrade"] == downgrade, "mandatory levels / J-A"
                assert env.banker == (before["banker"]-(2 if team == defend else 1))%4
                expected_match = defend if level == 14 and env.score < 80 and downgrade is None else None
                assert env.match_winner == expected_match, "A match end"
            metrics["tricks"] += 1
        metrics["plays"] += 1
    assert env.score == env.captured_score+env.penalty_score+env.bottom_score
    for seat in range(4):
        obs = env.observe(seat)
        assert "hands" not in obs and "deck" not in obs
        assert obs["kitty"] is None or env.kitty_public, "hidden bottom disclosure"


def one_round(job):
    seed, level, team, opponent, time_ms = job
    env = TractorEnv(GameConfig(level,False,0),seed)
    policies = [EnochPolicy(seed*4+p,time_ms=time_ms) if p%2 == team else
                RandomPolicy(seed*4+p) if opponent == "random" else
                EnochPolicy(seed*4+p,time_ms=0) for p in range(4)]
    metrics = Counter(actions=0,bids=0,burials=0,plays=0,tricks=0,failed_throws=0,
                      searched=0,fallbacks=0,changed_prior=0,decisions=0)
    trace = []
    started = time.monotonic()
    while env.phase not in ("round_end","match_end"):
        p = env.current_player
        obs = env.observe(p)
        before = env.export_state()  # auditor only, NEVER passed to the policy
        policy = policies[p]
        action = policy.act(obs)
        diag = None
        if env.phase == "playing" and p%2 == team:
            prior = EnochPolicy(seed*4+p,time_ms=0).act(obs)
            diag = policy.last_search.copy()
            metrics["decisions"] += 1
            metrics["searched"] += diag["worlds"] > 0
            metrics["fallbacks"] += diag["worlds"] == 0 and diag["candidates"] > 1
            metrics["changed_prior"] += Counter(c%54 for c in prior["cards"]) != Counter(c%54 for c in action["cards"])
        env.step(action)
        try:
            audit_transition(before,action,env,metrics)
        except AssertionError as exc:
            failure = Path("build")/f"ai-audit-failure-{seed}-{team}-{opponent}.json"
            failure.parent.mkdir(exist_ok=True)
            failure.write_text(json.dumps({"before":before,"action":action,"after":env.export_state()},ensure_ascii=False),encoding="utf-8")
            raise AssertionError(f"{exc}; {failure}") from exc
        public_action = {"type":"bury","count":8} if action["type"] == "bury" else action
        trace.append({"player":p,"phase":before["phase"],"action":public_action,"score":env.score,
                      "trick":before["trick_no"],"search":diag})
        if len(trace) > 500:
            raise RuntimeError("round did not finish")
    return {"seed":seed,"level":level,"enoch_team":team,"opponent":opponent,
            "won":env.result["winner"] == team,"result":env.result,
            "metrics":dict(metrics),"seconds":time.monotonic()-started,"trace":trace}


def evaluate(seeds=8, workers=3, time_ms=600):
    jobs = [(4000+i, [2,5,6,10,11,12,13,14][i%8],team,opponent,time_ms)
            for opponent in ("random","heuristic") for i in range(seeds) for team in (0,1)]
    records = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one_round,job):job for job in jobs}
        for future in as_completed(futures):
            record = future.result(); records.append(record)
            print(f"{len(records)}/{len(jobs)} {record['opponent']} seed={record['seed']} team={record['enoch_team']} win={record['won']}",flush=True)
    report = {"time_ms":time_ms,"worlds_limit":12,"workers":workers,
              "oracle":"independent rank/suit/structure/winner/penalty/settlement implementation",
              "scope":"balanced two-search-AI vs two-opponent-AI rounds; no human rating"}
    for opponent in ("random","heuristic"):
        matches = [r for r in records if r["opponent"] == opponent]
        metrics = Counter()
        for r in matches:
            metrics.update(r["metrics"])
        wins = sum(r["won"] for r in matches)
        paired = [sum(r["won"] for r in matches if r["seed"] == 4000+i)/2 for i in range(seeds)]
        report[opponent] = {"rounds":len(matches),"wins":wins,"win_rate":wins/len(matches),
                            "paired_seed_win_rates":paired,
                            "paired_seed_outcomes":{"wins":sum(x > .5 for x in paired),
                                                    "ties":sum(x == .5 for x in paired),
                                                    "losses":sum(x < .5 for x in paired)},
                            "audit":dict(metrics)}
    report["rounds"] = sorted(records,key=lambda r:(r["opponent"],r["seed"],r["enoch_team"]))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds",type=int,default=8)
    parser.add_argument("--workers",type=int,default=3)
    parser.add_argument("--time-ms",type=int,default=600)
    parser.add_argument("--output",type=Path,default=Path("build/ai-evaluation.json"))
    args = parser.parse_args()
    result = evaluate(args.seeds,args.workers,args.time_ms)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "rounds"},ensure_ascii=False,indent=2))
