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
from datetime import datetime
from pathlib import Path

from .cards import LABELS

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

    def append(self, env, actor, action, transition):
        self.frames.append(frame(env, actor, action, transition.info["events"],
                                 round((time.monotonic() - self.clock) * 1000)))


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

    def save(self, recording, *, mode, room, players, config, source="live", finished_at=None):
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
