"""Import complete private evaluation traces into the web replay archive.

This is a local operator tool, not a browser upload. Redacted public reports
cannot reconstruct buried cards. Validate every action and final outcome first.
"""
import argparse
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from tractor_sim.engine import TractorEnv, GameConfig
from tractor_sim.history import HistoryStore, RoundRecording

ROOT = Path(__file__).resolve().parent.parent


def import_evaluation(path, directory):
    path = Path(path)
    records = json.loads(path.read_text(encoding="utf-8"))["rounds"]
    store = HistoryStore(directory)
    existing = {m.get("room") for m in store.listing()["records"] if m["source"] == "imported-evaluation"}
    ended = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    imported = 0
    for index, item in enumerate(records):
        room = f"eval_{item['seed']}_{item['enoch_team']}_{item['opponent']}"
        if room in existing:
            continue
        env = TractorEnv(GameConfig(item["level"], False, 0), item["seed"])
        recording = RoundRecording(env)
        for trace in item["trace"]:
            action, actor = trace["action"], trace["player"]
            transition = env.step(action, actor)
            recording.append(env, actor, action, transition)
        if env.result != item["result"]:
            raise ValueError(f"{room} 结算结果与记录不一致")
        finished = ended + timedelta(microseconds=index)
        recording.started_at = (finished - timedelta(seconds=item["seconds"])).isoformat()
        players = [{"seat": p, "name": "Enoch 搜索" if p % 2 == item["enoch_team"] else
                    "随机策略" if item["opponent"] == "random" else "Enoch 启发式", "bot": True} for p in range(4)]
        store.save(recording, mode="evaluation", room=room, players=players, config=asdict(env.config),
                   source="imported-evaluation", finished_at=finished)
        imported += 1
    return imported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluation", type=Path, help="tools.evaluate_ai 保存的未脱敏完整记录")
    parser.add_argument("--directory", type=Path, default=ROOT / "runtime" / "history")
    args = parser.parse_args()
    print(f"Imported {import_evaluation(args.evaluation, args.directory)} complete rounds")


if __name__ == "__main__":
    main()
