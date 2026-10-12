"""Reproducible balanced Enoch-versus-Random round benchmark (not human Elo).

Run: python -m tools.benchmark_ai --seeds 16 --time-ms 250 --worlds 2
"""
import argparse
import json
import math
import time
from pathlib import Path
from tractor_sim import GameConfig, TractorEnv
from tractor_sim.enoch import EnochPolicy, AI_ID
from tractor_sim.policy import RandomPolicy


def benchmark(seeds, time_ms, worlds, start_seed=2000):
    results, batches, decisions, durations = [], 0, 0, []
    started = time.monotonic()
    for index in range(seeds):
        for team in (0,1):
            seed = start_seed+index
            level = 2+index%13
            env = TractorEnv(GameConfig(level,False,0),seed)
            policies = [EnochPolicy(seed*4+p,time_ms=time_ms,worlds=worlds)
                        if p%2 == team else RandomPolicy(seed*4+p) for p in range(4)]
            steps = 0
            while env.phase not in ("round_end","match_end"):
                p = env.current_player
                policy = policies[p]
                playing = env.phase == "playing"
                action = policy.act(env.observe(p))
                if playing and isinstance(policy,EnochPolicy):
                    decisions += 1; batches += policy.last_search["worlds"]
                    durations.append(policy.last_search["elapsed_ms"])
                env.step(action); steps += 1
                if steps > 500:
                    raise RuntimeError("round failed to finish")
            results.append({"seed":seed,"level":level,"enoch_team":team,
                            "enoch_won":env.result["winner"] == team,
                            "attacker_score":env.score,"steps":steps})
        print(f"finished {index+1}/{seeds} paired seeds",flush=True)
    wins = sum(r["enoch_won"] for r in results)
    n = len(results); p = wins/n; z = 1.96
    center = (p+z*z/(2*n))/(1+z*z/n)
    half = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    ordered = sorted(durations)
    return {"policy":AI_ID,"opponent":"RandomPolicy","seeds":seeds,"rounds":n,
            "wins":wins,"win_rate":p,"wilson_95":[center-half,center+half],
            "time_ms":time_ms,"worlds_limit":worlds,"search_batches":batches,
            "play_decisions":decisions,"mean_decision_ms":sum(durations)/max(1,decisions),
            "p95_decision_ms":ordered[min(len(ordered)-1,int(len(ordered)*.95))],
            "seconds":time.monotonic()-started,"results":results,
            "scope":"two Enoch seats versus two Random seats, balanced defender/attacker; not a human skill rating"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds",type=int,default=16)
    parser.add_argument("--time-ms",type=int,default=250)
    parser.add_argument("--worlds",type=int,default=2)
    parser.add_argument("--output",type=Path,default=Path("docs/ai-benchmark.json"))
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("seeds must be positive")
    data = benchmark(args.seeds,args.time_ms,args.worlds)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in data.items() if k != "results"},ensure_ascii=False,indent=2))
