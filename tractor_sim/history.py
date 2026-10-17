"""Persistent, completed-round snapshots. No active round is published.

Snapshots keep replays independent of future rule/policy changes. Private
burial actions stay on disk; the HTTP projection reveals the final bottom
only at settlement. Recording uses actual accepted transitions, including bots.
"""
import copy
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .cards import LABELS
from .engine import TractorEnv, GameConfig, MANDATORY, MANDATORY_COMPLETION_RULE

LOG = logging.getLogger(__name__)
END_PHASES = ("round_end", "match_end")
FIELDS = ("phase", "current_player", "turn_direction", "level", "levels", "banker", "auction", "trump", "bid",
          "hands", "kitty_public", "kitty_owner", "deal_count", "redeals", "trick",
          "trick_no", "last_trick", "score", "captured_score", "penalty_score",
          "bottom_score", "result", "match_winner")


def now():
    return datetime.now().astimezone()


def frame(env, actor=None, action=None, events=(), elapsed=0):
    state = copy.deepcopy({key: getattr(env, key) for key in FIELDS})
    state.update(round=env.round_no, kitty=env.kitty[:] if env.kitty_public else None,
                 passed_levels=[sorted(p) for p in env.passed],
                 mandatory_completion_rule=env.mandatory_completion_rule,
                 trick_winner=env._trick_winner() if len(env.trick) >= 2 else None,
                 drawn_card=env.deck[env.deal_count - 1] if env.phase == "dealing" else None)
    if env.phase == "dealing" and env.auction and not env.bid:
        state["banker"] = None
    if env.phase in END_PHASES:
        state["settlement_kitty"] = env.kitty[:]
    return {"actor": actor, "action": copy.deepcopy(action), "at_ms": elapsed,
            "events": copy.deepcopy(list(events)), "state": state}


class RoundRecording:
    def __init__(self, env):
        self.started_at = now().isoformat()
        self.clock = time.monotonic()
        self.frames = [frame(env, events=[e for e in env.events
                                         if e["round"] == env.round_no])]
        self.saved_id = None
        self.progress = env.export_progress() if env.phase in END_PHASES else None

    def append(self, env, actor, action, transition):
        self.frames.append(frame(env, actor, action, transition.info["events"],
                                 round((time.monotonic() - self.clock) * 1000)))
        if env.phase in END_PHASES:
            self.progress = env.export_progress()


class HistoryStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.index = {}
        # Scan only metadata once at startup, not on every poll/list request.
        if self.directory.is_dir():
            for path in self.directory.glob("*.json"):
                try:
                    doc = json.loads(path.read_text(encoding="utf-8"))
                    self._check(doc)
                    self.index[doc["meta"]["id"]] = (path, doc["meta"])
                except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
                    LOG.warning("Ignoring invalid replay %s: %s", path.name, exc)

    def record_validation_error(self, entry):
        """Server-local diagnostics, never served by the public history API."""
        stamp = now()
        directory = self.directory.parent / 'diagnostics'
        try:
            with self.lock:
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / f'validation-errors-{stamp:%Y-%m-%d}.jsonl'
                with path.open('a', encoding='utf-8') as output:
                    output.write(json.dumps({'time':stamp.isoformat(), **entry}, ensure_ascii=False) + '\n')
        except OSError as exc:
            LOG.warning('Could not save validation diagnostics: %s', exc)

    @staticmethod
    def _check(doc):
        meta, frames = doc["meta"], doc["frames"]
        if (doc.get("schema_version") != 1 or
                not re.fullmatch(r"[a-f0-9]{32}", meta["id"]) or
                not frames or frames[-1]["state"]["phase"] not in END_PHASES or
                frames[-1]["state"]["result"] != meta["result"]):
            raise ValueError("不是完整的对局回放记录")

    def save(self, recording, *, mode, room, players, config, source="live", finished_at=None, continuation=None):
        if recording.saved_id:
            return recording.saved_id
        ended = finished_at or now()
        state = recording.frames[-1]["state"]
        if state["phase"] not in END_PHASES:
            raise ValueError("只能保存已完成的对局")
        identifier = secrets.token_hex(16)
        filename = f"打{LABELS[state['level']]}_{ended:%Y-%m-%d_%H-%M-%S-%f}_{identifier[:8]}.json"
        meta = {"id": identifier, "filename": filename, "mode": mode, "room": room,
                "players": copy.deepcopy(players), "config": copy.deepcopy(config),
                "source": source, "started_at": recording.started_at,
                "finished_at": ended.isoformat(), "level": state["level"],
                "trump": state["trump"], "round": state["round"],
                "steps": len(recording.frames) - 1, "tricks": state["trick_no"],
                "result": copy.deepcopy(state["result"])}
        if getattr(recording, 'progress', None) is not None:
            meta['progress'] = copy.deepcopy(recording.progress)
        if continuation is not None:
            meta['continued_from'] = continuation['source_id']
        doc = {"schema_version": 1, "meta": meta, "frames": recording.frames}
        self._check(doc)
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.directory / filename
            temporary = target.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            self.index[identifier] = (target, meta)
            recording.saved_id = identifier
        return identifier

    def listing(self):
        with self.lock:
            return {"schema_version": 1, "records": copy.deepcopy(sorted(
                (meta for _, meta in self.index.values()),
                key=lambda m: m["finished_at"], reverse=True))}

    def read(self, identifier):
        # IDs are looked up in an index. No user-supplied path is resolved.
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise KeyError(identifier)
        with self.lock:
            entry = self.index.get(identifier)
        if entry is None:
            raise KeyError(identifier)
        doc = json.loads(entry[0].read_text(encoding="utf-8"))
        self._check(doc)
        final_kitty = None
        for item in doc["frames"]:
            if item["action"] and item["action"]["type"] == "bury":
                final_kitty = item["action"].get("cards", final_kitty)
                item["action"] = {"type": "bury", "count": 8}
            state = item["state"]
            if state["kitty_public"] and state["kitty"]:
                final_kitty = state["kitty"]
            # Upgrade old recordings from the last actual burial, not the
            # original deal. Never project this field into an earlier frame.
            state.pop("settlement_kitty", None)
            if state["phase"] in END_PHASES and final_kitty is not None:
                state["settlement_kitty"] = list(final_kitty)
            if not state["kitty_public"]:
                state["kitty"] = None
                if state["result"]:
                    state["result"]["kitty"] = None
        return doc

    def continuation(self, identifier):
        """Restore only a completed hand's public match progress, never its deal."""
        doc = self.read(identifier)
        meta, state = doc['meta'], doc['frames'][-1]['state']
        result = meta['result']
        summary = {'source_id': identifier, 'finished_at': meta['finished_at'],
                   'source_round': meta['round'], 'source_level': meta['level'],
                   'source_score': result['score'], 'previous_players': copy.deepcopy(meta['players'])}
        if state['phase'] == 'match_end' or result.get('match_winner') is not None:
            return {**summary, 'available': False, 'message': '这场比赛已打完 A 并结束，请新建比赛'}
        try:
            progress = copy.deepcopy(meta.get('progress'))
            if progress is None and state.get('mandatory_completion_rule') == MANDATORY_COMPLETION_RULE:
                progress = {'schema_version': 1, 'completed_round': meta['round'],
                    'levels': result['levels_after'][:], 'next_banker': result['next_banker'],
                    'passed_levels': copy.deepcopy(state['passed_levels']),
                    'mandatory_completion_rule': MANDATORY_COMPLETION_RULE}
            if progress is None:
                progress = self._legacy_progress(meta)
            TractorEnv.validate_progress(progress)
            if progress['completed_round'] != meta['round'] or progress['next_banker'] != result['next_banker']:
                raise ValueError('回放的结算进度不一致')
        except (ValueError, KeyError, TypeError) as exc:
            return {**summary, 'available': False, 'message': '无法可靠恢复这份记录：' + str(exc)}
        return {**summary, 'available': True, 'progress': progress,
                'next_round': progress['completed_round'] + 1, 'levels': progress['levels'][:],
                'passed_levels': copy.deepcopy(progress['passed_levels']),
                'next_banker': progress['next_banker'],
                'next_level': progress['levels'][progress['next_banker'] % 2],
                'mandatory_corrected': progress['levels'] != result['levels_after']}

    def _legacy_progress(self, meta):
        # Old frames did not store permanent win credit. Reconstruct it only
        # from a complete, chronological match in this same room/configuration.
        def stamp(value):
            parsed = datetime.fromisoformat(value)
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()

        with self.lock:
            matches = [copy.deepcopy(m) for _, m in self.index.values()
                       if m['room'] == meta['room'] and m['mode'] == meta['mode'] and
                       m['config'] == meta['config'] and stamp(m['finished_at']) <= stamp(meta['finished_at'])]
        starts = [m for m in matches if m['round'] == 1 and stamp(m['started_at']) <= stamp(meta['started_at'])]
        if not starts:
            raise ValueError('旧回放缺少比赛第一局，无法核对必打级的坐庄获胜记录')
        start = max(starts, key=lambda m: stamp(m['started_at']))
        chain = [m for m in matches if stamp(m['started_at']) >= stamp(start['started_at'])]
        selected = []
        for number in range(1, meta['round'] + 1):
            candidates = [m for m in chain if m['round'] == number]
            if len(candidates) != 1:
                raise ValueError(f'旧回放第 {number} 局缺失或重复，无法核对获胜记录')
            selected.append(candidates[0])
        passed = [set(), set()]
        floors = GameConfig(**meta['config']).starting_levels
        previous_end = None
        for item in selected:
            if previous_end is not None and stamp(item['started_at']) < previous_end:
                raise ValueError('旧回放的比赛时间顺序不一致')
            previous_end = stamp(item['finished_at'])
            result = item['result']; defense = result['banker'] % 2
            if result['score'] < 80:
                passed[defense].add(result['level'])
            floors = [min(floors[t], result['levels_after'][t]) for t in range(2)]
        levels = meta['result']['levels_after'][:]
        for team in range(2):
            levels[team] = next((r for r in sorted(MANDATORY)
                if floors[team] <= r < levels[team] and r not in passed[team]), levels[team])
        return {'schema_version': 1, 'completed_round': meta['round'], 'levels': levels,
                'passed_levels': [sorted(p) for p in passed],
                'next_banker': meta['result']['next_banker'],
                'mandatory_completion_rule': MANDATORY_COMPLETION_RULE}
