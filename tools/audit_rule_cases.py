"""Rare table-rule cases checked by the independent evaluation oracle."""
import copy
import json
from collections import Counter
from pathlib import Path

from tractor_sim import GameConfig, RandomPolicy, TractorEnv
from .evaluate_ai import audit_transition, check_play, door, point, units


def card(s, r, pair=False):
    f = "SHDC".index(s)*13+r-2
    return [f, f+54] if pair else [f]


def step(env, action, counts):
    before = env.export_state()
    env.step(action)
    audit_transition(before, action, env, counts)


def deal(reserved, banker=0, bidder=0):
    hands = copy.deepcopy(reserved)
    used = sum(hands, [])
    assert len(used) == len(set(used))
    rest = iter(c for c in range(108) if c not in used)
    for hand in hands:
        hand.extend(next(rest) for _ in range(25-len(hand)))
    deck = [hands[(banker-p)%4][i] for i in range(25) for p in range(4)]+list(rest)
    env = TractorEnv(GameConfig(2,False,banker),0)
    env.deck = deck; env.kitty = deck[100:]; env.hands = [[],[],[],[]]
    env.hands[banker] = deck[:1]
    return env


def counter_case(counts, different_owner=False):
    env = deal([card('H',2),card('S',2,True),[52,106],[53,107]],
               banker=1 if different_owner else 0)
    initial = card('H',2)
    while env.phase == 'dealing':
        action = {'type':'bid','cards':initial} if env.current_player == 0 and env.bid is None and set(initial) <= set(env.hands[0]) else {'type':'pass'}
        step(env,action,counts)
    while env.phase == 'final_bidding':step(env,{'type':'pass'},counts)
    original = env.hands[env.current_player][-8:]
    while env.phase in ('burying','countering'):
        p = env.current_player
        if env.phase == 'burying':
            # Preserve each reserved counter while changing the actual bottom.
            protected = set([[13],[0,54],[52,106],[53,107]][p])
            selected = [c for c in env.hands[p] if c not in protected][:8]
            step(env,{'type':'bury','cards':selected},counts)
        else:
            reserved = {1:card('S',2,True),2:[52,106],3:[53,107]}
            cs = reserved.get(p)
            strength = {1:2,2:3,3:4}.get(p,0)
            step(env,{'type':'bid','cards':cs} if cs and strength > env.bid['strength'] else {'type':'pass'},counts)
    assert env.current_player == env.config.banker
    assert env.bid['strength'] == 4 and env.kitty_owner == 3
    assert env.kitty != original


def ending(hands, level=6, trump='H', score=0):
    env = TractorEnv(GameConfig(level,False,0),1)
    env.phase = 'playing'; env.level = level; env.trump = trump
    env.hands = copy.deepcopy(hands); env.current_player = 0
    used = sum(hands,[])
    rest = [c for c in range(108) if c not in used]
    env.kitty = [c for c in rest if point(c) == 0][:8]
    env.played = [c for c in rest if c not in env.kitty]
    env.deal_count = 100; env.kitty_owner = 0
    env.score = env.captured_score = score
    env.assert_invariants()
    return env


def terminal_cases(counts):
    for score in (0,1,39,40,79,80,119,120,159,160,200):
        env = ending([card('C',3),[52],card('C',4),card('C',2)],score=score)
        while env.phase == 'playing':step(env,{'type':'play','cards':env.hands[env.current_player][:]},counts)
    for level in (11,14):
        for score in (0,79,80,120):
            env = ending([card('C',3),card('H',level),card('C',4),card('C',2)],level=level,score=score)
            while env.phase == 'playing':step(env,{'type':'play','cards':env.hands[env.current_player][:]},counts)
            assert env.result['downgrade'] == (2 if level == 11 else 11)
        env = ending([[52],card('H',2),card('H',3),card('H',4)],level=level)
        while env.phase == 'playing':step(env,{'type':'play','cards':env.hands[env.current_player][:]},counts)
        assert env.match_winner == (0 if level == 14 else None)
    # Ruffing a pair + single compares the pair, ignoring the bigger joker single.
    env = ending([card('S',12,True)+card('S',3),card('H',8,True)+[53],
                  card('H',9,True)+[52],card('H',7,True)+card('H',3)])
    while env.phase == 'playing':step(env,{'type':'play','cards':env.hands[env.current_player][:]},counts)
    assert env.result['last_winner'] == 2
    # The large joker single cannot compensate for a weaker tractor; a gapped
    # pair selection cannot ruff at all, despite having an even larger joker.
    env = ending([card('S',8,True)+card('S',9,True)+card('S',12),
                  card('H',8,True)+card('H',9,True)+[53],
                  card('H',10,True)+card('H',12,True)+[52],
                  card('H',13,True)+card('H',14,True)+card('H',3)])
    while env.phase == 'playing':step(env,{'type':'play','cards':env.hands[env.current_player][:]},counts)
    assert env.result['last_winner'] == 3 and env.result['multiplier'] == 8


def structure_case(counts):
    lead = card('S',8,True)+card('S',9,True)+card('S',10,True)
    held = card('S',3,True)+card('S',4,True)+card('S',12,True)+card('S',13)+card('S',14)
    env = ending([lead+card('S',2)+card('C',2),
                  card('C',3,True)+card('C',4,True)+card('C',7,True)+card('C',8)+card('C',9),
                  card('H',3,True)+card('H',4,True)+card('H',7,True)+card('H',9)+card('H',10),
                  held],trump='D')
    step(env,{'type':'play','cards':lead},counts)
    before = env.export_state()
    bad = card('S',3,True)+card('S',12,True)+card('S',13)+card('S',14)
    try:check_play(held,bad,env.trick,6,'D')
    except AssertionError:pass
    else:raise AssertionError('independent oracle accepted broken follow structure')
    assert not env.validate_action({'type':'play','cards':bad})['valid']
    assert env.export_state() == before
    good = card('S',3,True)+card('S',4,True)+card('S',12,True)
    step(env,{'type':'play','cards':good},counts)
    for _ in range(2):step(env,{'type':'play','cards':env.hands[env.current_player][:6]},counts)
    assert env.last_trick['winner'] == 0


def aggressive_rounds(counts):
    for seed in range(12):
        env = TractorEnv(GameConfig([2,6,11,14][seed%4],seed%2 == 0,0),seed+8000)
        policy = RandomPolicy(seed)
        actions = 0
        while env.phase not in ('round_end','match_end'):
            action = policy.act(env.observe(env.current_player))
            if env.phase == 'playing' and not env.trick:
                hand = env.hands[env.current_player]
                groups = [[c for c in hand if door(c,env.level,env.trump) == d] for d in ('S','H','D','C','T')]
                attempts = [g[:7] for g in groups if len(g) >= 2 and len(units(g[:7],env.level,env.trump)) > 1]
                if attempts:action = {'type':'play','cards':max(attempts,key=len)}
            step(env,action,counts); actions += 1
            assert actions < 1000


def run():
    counts = Counter()
    for auction in (False,True):
        env = TractorEnv(GameConfig(6,auction,0),123)
        for _ in range(100):step(env,{'type':'pass'},counts)
        assert env.redeals == int(auction)
    counter_case(counts)
    counter_case(counts,different_owner=True)
    terminal_cases(counts)
    structure_case(counts)
    aggressive_rounds(counts)
    report = {'no_bid_fixtures':2,'counter_fixtures':2,'terminal_fixtures':23,'structure_fixtures':1,'aggressive_complete_rounds':12,
              'checks':dict(counts),'oracle':'tools.evaluate_ai; no tractor_sim.rules imports'}
    Path('build').mkdir(exist_ok=True)
    Path('build/ai-rule-cases.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':run()
