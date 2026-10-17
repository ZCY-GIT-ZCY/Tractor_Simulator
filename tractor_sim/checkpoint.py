"""Private room checkpoints for rolling worker replacements (never replays)."""
import copy
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from .engine import GameConfig, TractorEnv
from .history import RoundRecording
from .enoch import EnochPolicy
from .policy import RandomPolicy

SCHEMA = 1
ROOM_FIELDS = (
    'code', 'mode', 'replay_id', 'history_error', 'review_round',
    'review_started', 'review_confirmed_by', 'clients', 'departed_names',
    'started', 'host', 'autoplay', 'policy_epochs', 'paused', 'policy_error',
    'policy_error_actor', 'version', 'diagnostic_revision', 'updated',
    'next_bot_at', 'bidding_deadline', 'bidding_paused_at', 'ai_name', 'ai_id',
    'review_confirmations', 'review_ai_banker', 'trick_review_until', 'record_plays',
    'vacant_since', 'retired',
    'continuation',
)
CLOCK_FIELDS = ('review_started', 'updated', 'next_bot_at',
                 'bidding_deadline', 'bidding_paused_at', 'trick_review_until', 'vacant_since')


def tuples(value):
    return tuple(map(tuples, value)) if isinstance(value, (tuple, list)) else value


def private_json(path, value):
    """Atomic owner-only file; operational directory must also be private."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def policy_state(policy):
    if type(policy) is EnochPolicy:
        kind = 'enoch'
    elif type(policy) is RandomPolicy:
        kind = 'random'
    else:
        raise ValueError('该自定义策略尚未实现 checkpoint，拒绝丢失策略状态')
    return {'kind': kind, 'rng': policy.rng.getstate(),
            'attributes': copy.deepcopy({k: v for k, v in vars(policy).items() if k != 'rng'})}


def restore_policy(state):
    if state['kind'] == 'enoch':
        policy = EnochPolicy()
    elif state['kind'] == 'random':
        policy = RandomPolicy()
    else:
        raise ValueError('不支持的策略 checkpoint')
    policy.__dict__.update(copy.deepcopy(state['attributes']))
    policy.rng.setstate(tuples(state['rng']))
    return policy


def shift_clocks(room, seconds):
    for field in CLOCK_FIELDS:
        if getattr(room, field) is not None:
            setattr(room, field, getattr(room, field) + seconds)
    for client in room.clients.values():
        client['seen'] += seconds
        if 'legacy_seen' in client:
            client['legacy_seen'] += seconds
        for page in client.get('pages', {}).values():
            page['seen'] += seconds
            if page['closed_at'] is not None:
                page['closed_at'] += seconds
    if room.recording:
        room.recording.clock += seconds


def dump_registry(registry):
    if registry.frozen_at is None:
        raise RuntimeError('必须先冻结 worker')
    rooms = []
    with registry.lock:
        for room in registry.rooms.values():
            with room.condition:
                supported = set(ROOM_FIELDS) | {'config', 'env', 'history', 'recording',
                    'policies', 'policy', 'pending', 'ai_seats', 'diagnostic_keys',
                    'bidding_window_key', 'grace_policy_seen', 'condition'}
                if set(vars(room)) - supported:
                    raise ValueError('房间含尚未适配 checkpoint 的字段，拒绝丢失状态')
                if room.pending:
                    raise RuntimeError('AI 计算尚未排空')
                state = {field: copy.deepcopy(getattr(room, field)) for field in ROOM_FIELDS}
                state.update(config=asdict(room.config), env=room.env.export_state(),
                             ai_seats=sorted(room.ai_seats),
                             diagnostic_keys=list(room.diagnostic_keys),
                             grace_policy_seen=list(room.grace_policy_seen),
                             bidding_window_active=room.bidding_window_key is not None,
                             policies=[policy_state(p) for p in room.policies],
                             policy=policy_state(room.policy),
                             recording=copy.deepcopy(vars(room.recording)) if room.recording else None)
                rooms.append(state)
    return {'schema': SCHEMA, 'clock': registry.frozen_at, 'rooms': rooms,
            'max_rooms': registry.max_rooms, 'idle_seconds': registry.idle_seconds}


def load_registry(registry, document):
    from .server import Room
    if document.get('schema') != SCHEMA:
        raise ValueError('不兼容的房间 checkpoint，保留旧 worker')
    rooms, tokens = {}, {}
    restored_at = time.monotonic()
    shift = restored_at - document['clock']
    for state in document['rooms']:
        room = Room(state['code'], state['mode'], GameConfig(**state['config']), history=registry.history)
        for field in ROOM_FIELDS:
            setattr(room, field, copy.deepcopy(state.get(field, getattr(room, field))))
        room.env = TractorEnv.from_state(state['env'])
        room.ai_seats = set(state['ai_seats'])
        room.departed_names = {int(k): v for k, v in room.departed_names.items()}
        room.diagnostic_keys = {(p, tuple(cs)) for p, cs in state['diagnostic_keys']}
        room.grace_policy_seen = {tuple(pair) for pair in state['grace_policy_seen']}
        room.bidding_window_key = ((room.env, room.env.round_no, room.env.redeals)
                                  if state['bidding_window_active'] else None)
        room.policies = [restore_policy(p) for p in state['policies']]
        if len(room.policies) != 4:
            raise ValueError('策略座位不完整')
        room.policy = restore_policy(state['policy'])
        if state['recording'] is not None:
            room.recording = RoundRecording.__new__(RoundRecording)
            room.recording.__dict__.update(copy.deepcopy(state['recording']))
        else:
            room.recording = None
        shift_clocks(room, shift)
        if room.code in rooms:
            raise ValueError('重复房间')
        rooms[room.code] = room
        for token in room.clients:
            if token in tokens:
                raise ValueError('重复座位凭据')
            tokens[token] = room
    # Commit only after every room passed restoration and engine invariants.
    with registry.lock:
        registry.rooms, registry.tokens = rooms, tokens
        registry.max_rooms = document['max_rooms']
        # Older workers used a two-hour room expiry. Apply the new upper bound
        # even when restoring their checkpoints during a live replacement.
        registry.idle_seconds = min(registry.idle_seconds, document['idle_seconds'])
        registry.frozen_at = restored_at
        registry.frozen.set()
