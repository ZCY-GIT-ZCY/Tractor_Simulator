"""Standard-library room server. Server owns state and redacts every response.

Clients use authenticated long polling and optimistic revisions. Four-player
rooms pause on disconnect; a reconnect retains the original seat and hand.
"""
import json
import random
import re
import secrets
import socket
import threading
import time
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from .engine import TractorEnv, GameConfig
from .policy import RandomPolicy
from .enoch import EnochPolicy, AI_ID, AI_NAME
from .history import HistoryStore, RoundRecording
from .rules import IllegalAction
from .cards import points

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DISCONNECT_SECONDS = 25
ROOM_IDLE_SECONDS = 2 * 60 * 60
AI_REVIEW_SECONDS = 6
AI_TRICK_REVIEW_SECONDS = 2
BIDDING_GRACE_SECONDS = 6
ROOM_MEMBER_LIMIT = 16


class Room:
    def __init__(self, code, mode, config, seed=None, policy_factory=None, history=None):
        self.code, self.mode, self.config = code, mode, config
        self.env = TractorEnv(config, seed)
        self.history = history
        self.recording = RoundRecording(self.env) if history else None
        self.replay_id = None
        self.history_error = None
        self.review_round = None
        self.review_started = None
        self.review_confirmed_by = None
        self.review_confirmations = []
        self.review_ai_banker = False
        self.trick_review_until = None
        self.record_plays = False
        factory = policy_factory or (EnochPolicy if mode in ("pve", "pvp") else RandomPolicy)
        # Separate instances ensure future policy memory cannot mix private seats.
        seat_rng = random.Random(seed)
        self.policies = [factory(None if seed is None else seat_rng.getrandbits(64)) for _ in range(4)]
        self.policy = RandomPolicy(seed)  # explicit random-step button / self mode
        self.ai_name = AI_NAME if factory is EnochPolicy else getattr(self.policies[0], "name", "随机策略")
        self.ai_id = getattr(self.policies[0], "id", "custom")
        self.pending = None
        self.clients = {}
        self.ai_seats = set()
        self.departed_names = {}
        self.started = mode != "pvp"
        self.host = None
        self.autoplay = False
        self.policy_epochs = [0] * 4
        self.paused = False
        self.policy_error = False
        self.policy_error_actor = None
        self.version = 0
        self.diagnostic_revision = None
        self.diagnostic_keys = set()
        self.updated = time.monotonic()
        self.next_bot_at = time.monotonic() + 0.4
        self.bidding_window_key = None
        self.bidding_deadline = None
        self.bidding_paused_at = None
        self.grace_policy_seen = set()
        self.condition = threading.Condition(threading.RLock())

    def bump(self):
        self.version += 1
        self.updated = time.monotonic()
        self.condition.notify_all()

    def sync_bidding_window(self, now=None):
        """Room-lock protected clock; offline engine rollouts need no sleeps."""
        now = time.monotonic() if now is None else now
        if not self.started or self.env.phase != 'dealing' or self.env.deal_count < 100:
            self.bidding_window_key = self.bidding_deadline = self.bidding_paused_at = None
            self.grace_policy_seen.clear()
            return
        key = (self.env, self.env.round_no, self.env.redeals)
        if self.bidding_window_key != key:
            self.bidding_window_key = key
            self.bidding_deadline = now + BIDDING_GRACE_SECONDS
            self.bidding_paused_at = now if self.paused else None
            self.grace_policy_seen.clear()
        elif self.paused and self.bidding_paused_at is None:
            self.bidding_paused_at = now
        elif not self.paused and self.bidding_paused_at is not None:
            self.bidding_deadline += now - self.bidding_paused_at
            self.bidding_paused_at = None

    def close_bidding_if_due(self, now=None):
        now = time.monotonic() if now is None else now
        self.sync_bidding_window(now)
        if self.bidding_deadline is None or self.paused or now < self.bidding_deadline:
            return False
        # Exactly the same engine transition, log and replay as a final pass.
        self.step({'type':'pass'}, now=now)
        self.next_bot_at = now + 0.55
        self.bump()
        return True

    def step(self, action, player=None, *, now=None):
        now = time.monotonic() if now is None else now
        self.sync_bidding_window(now)
        if isinstance(action, dict) and self.bidding_deadline is not None:
            if action.get('type') == 'pass' and now < self.bidding_deadline:
                raise IllegalAction('BIDDING_WAIT', '牌已摸完，等待六秒实时亮主结束；无需继续摸牌')
            if action.get('type') == 'bid' and now >= self.bidding_deadline:
                raise IllegalAction('BIDDING_CLOSED', '六秒实时亮主已结束，请按后续轮询行动')
        actor = self.env.current_player if player is None else player
        old_round = self.env.round_no
        old_trick = self.env.trick_no
        transition = self.env.step(action, actor)
        self.sync_bidding_window(now)
        if self.env.round_no != old_round:
            self.review_round = self.review_started = self.review_confirmed_by = None
            self.review_confirmations = []
            self.review_ai_banker = False
            self.trick_review_until = None
        elif self.env.phase in ("round_end", "match_end"):
            self._ensure_review()
            self.trick_review_until = None
        elif self.env.phase == 'playing':
            if self.env.trick_no != old_trick:
                self.trick_review_until = now + AI_TRICK_REVIEW_SECONDS
            elif self.env.trick:
                self.trick_review_until = None
        if self.history:
            if self.env.round_no != old_round:
                self.recording = RoundRecording(self.env)
                self.replay_id = self.history_error = None
            else:
                self.recording.append(self.env, actor, action, transition)
                if self.env.phase in ("round_end", "match_end"):
                    players = []
                    for seat in range(4):
                        human = next((c for c in self.clients.values() if c["seat"] == seat), None)
                        bot = self.is_bot(seat)
                        players.append({"seat": seat, "name": self.player_name(seat, human, bot), "bot": bot})
                    try:
                        self.replay_id = self.history.save(self.recording, mode=self.mode,
                            room=self.code, players=players, config=asdict(self.config))
                    except OSError as exc:
                        self.history_error = "回放保存失败，请检查服务端存储空间和日志"
                        print(f"History error in {self.code}: {exc}", flush=True)
        return transition

    def record_validation_error(self, player, action, response, context=None):
        if not self.history or response.get('code') not in ('FOLLOW_SUIT', 'FOLLOW_STRUCTURE'):
            return
        env = self.env
        if self.diagnostic_revision != env.revision:
            self.diagnostic_revision = env.revision
            self.diagnostic_keys.clear()
        key = (player, tuple(sorted(action.get('cards', []))))
        if key in self.diagnostic_keys or len(self.diagnostic_keys) >= 32:
            return
        self.diagnostic_keys.add(key)
        self.history.record_validation_error({'room':self.code, 'version':self.version,
            'revision':env.revision, 'round':env.round_no, 'trick_no':env.trick_no,
            'player':player, 'level':env.level, 'trump':env.trump,
            'lead':env.trick[0]['cards'][:] if env.trick else [],
            'hand':env.hands[player][:], 'selected':action['cards'][:],
            'code':response['code'], 'message':response['message'],
            'displayed':context})

    def _ensure_review(self):
        """Presentation acknowledgment is separate from engine/training actions."""
        result = self.env.result
        if result and self.env.phase in ("round_end", "match_end") and self.review_round != result["round"]:
            self.review_round = result["round"]
            self.review_started = time.monotonic()
            self.review_confirmed_by = None
            self.review_confirmations = []
            self.review_ai_banker = self.is_bot(result['banker'])

    def review_humans(self):
        return {token: client for token, client in self.clients.items() if client['seat'] is not None}

    def ai_review(self):
        # Replacing an AI between hands does not change who actually played
        # banker. A departing human banker can instead hand review to the AI.
        return self.review_ai_banker or self.is_bot(self.env.result['banker'])

    def settlement(self, client):
        if self.env.phase not in ("round_end", "match_end") or not self.env.result:
            return None
        self._ensure_review()
        banker = self.env.result["banker"]
        ai_banker = self.ai_review()
        humans = self.review_humans()
        acknowledgments = [{"seat": c['seat'], "name": c['name'],
                            "confirmed": token in self.review_confirmations}
                           for token, c in humans.items()]
        own_confirmed = any(c is client and token in self.review_confirmations for token, c in humans.items())
        return {"round": self.review_round, "banker": banker,
                "kitty": self.env.kitty[:], "kitty_points": sum(points(c) for c in self.env.kitty),
                "confirmed": self.review_confirmed_by is not None,
                "confirmed_by": self.review_confirmed_by,
                "can_confirm": (client['seat'] is not None and (ai_banker or self.mode == "self" or client["seat"] == banker))
                    and not own_confirmed and self.review_confirmed_by is None,
                "own_confirmed": own_confirmed,
                "humans": acknowledgments,
                "pending_humans": [a for a in acknowledgments if not a['confirmed']],
                "ai_banker": ai_banker,
                "ai_confirm_seconds": max(0, AI_REVIEW_SECONDS - (time.monotonic() - self.review_started)) if ai_banker else None}

    def confirm_result(self, token, round_no=None):
        client = self.check(token)
        review = self.settlement(client)
        if round_no is not None and (review is None or round_no != review['round']):
            raise IllegalAction('REVIEW_STALE', '确认对应的牌局已结束，请核对当前牌桌')
        if review is None:
            raise IllegalAction("PHASE", "本局尚未结束")
        if review['confirmed'] or token in self.review_confirmations:
            return
        if not review["can_confirm"]:
            raise IllegalAction("BANKER_ONLY", "请由本局坐庄的玩家确认底牌和计分")
        if not review["confirmed"]:
            self.review_confirmations.append(token)
            if review['ai_banker']:
                self.finish_ai_review()
            else:
                self.review_confirmed_by = review["banker"]
            self.bump()

    def finish_ai_review(self):
        if self.env.phase not in ('round_end', 'match_end') or not self.env.result:
            return False
        self._ensure_review()
        if not self.ai_review():
            return False
        humans = self.review_humans()
        if any(t not in self.review_confirmations for t in humans):
            return False
        # An empty room must not start an endless stream of autonomous hands.
        if not humans and time.monotonic() - self.review_started < AI_REVIEW_SECONDS:
            return False
        if humans and self.env.phase == 'round_end' and self.paused:
            return False
        changed = self.review_confirmed_by is None
        self.review_confirmed_by = self.env.result['banker']
        if humans and self.env.phase == 'round_end' and not self.paused:
            self.step({'type': 'next_round'})
            self.next_bot_at = time.monotonic() + 0.55
            changed = True
        return changed

    def set_record_plays(self, token, enabled):
        self.check(token)
        if token != self.host:
            raise IllegalAction('HOST_ONLY', '请由房主决定是否在牌局动态中记牌')
        if type(enabled) is not bool:
            raise ValueError('enabled 须为布尔值')
        if self.record_plays != enabled:
            self.record_plays = enabled
            self.bump()

    def trick_review_remaining(self, now=None):
        if (self.trick_review_until is None or self.env.phase != 'playing' or self.env.trick
                or not self.policy_controls(self.env.current_player)):
            return 0
        return max(0, self.trick_review_until - (time.monotonic() if now is None else now))

    def join(self, name, seat=None):
        with self.condition:
            if self.mode == "pvp":
                self.require_admission()
                if len(self.clients) >= ROOM_MEMBER_LIMIT:
                    raise IllegalAction("ROOM_FULL", "等候区已满，请换房间号")
                seat = None  # Room membership does not claim a chair.
            else:
                occupied = {c["seat"] for c in self.clients.values()}
                if seat is None:
                    seat = next((s for s in range(4) if s not in occupied), None)
                if seat is None or seat in occupied:
                    raise IllegalAction("ROOM_FULL", "房间已满，请换房间号")
            token = secrets.token_urlsafe(32)
            self.clients[token] = {"seat": seat, "name": name[:24], "seen": time.monotonic(), "takeover": False}
            if self.host is None:
                self.host = token
            self.refresh_pause()
            self.bump()
            return token

    def seat_selection_open(self):
        return not self.started or self.env.phase == "round_end"

    def require_admission(self):
        if self.env.phase == "match_end":
            raise IllegalAction("MATCH_FINISHED", "比赛已结束，请创建新房间")
        if not self.seat_selection_open():
            raise IllegalAction("IN_PROGRESS", "本局正在进行，请在两局之间加入并替换 AI")
        if len({c["seat"] for c in self.clients.values() if c["seat"] is not None}) == 4:
            raise IllegalAction("ROOM_FULL", "四席均为真人，房间已满")

    def elect_host(self):
        """An occupied human chair takes precedence over a waiting member."""
        self.host = next((t for t, c in self.clients.items() if c["seat"] is not None),
                         next(iter(self.clients), None))

    def leave(self, token):
        """Explicit departure releases ownership; an active hand keeps its AI chair."""
        client = self.check(token)
        seat = client["seat"]
        del self.clients[token]
        if seat is not None:
            self.invalidate_policy_control({seat})
            if self.mode == "pvp":
                self.ai_seats.add(seat)
                self.departed_names[seat] = client['name']
            if self.policy_error_actor == seat:
                self.policy_error = False
                self.policy_error_actor = None
        if token == self.host:
            self.elect_host()
        self.next_bot_at = time.monotonic() + 0.15
        self.refresh_pause()
        self.finish_ai_review()
        self.bump()

    def occupied_seats(self):
        return {c["seat"] for c in self.clients.values() if c["seat"] is not None} | self.ai_seats

    def player_name(self, seat, human, bot):
        if human:
            return human['name']
        if bot:
            return (self.departed_names[seat] + '（AI 接替）' if seat in self.departed_names else
                    f'{self.ai_name} {seat + 1}')
        return f'座位 {seat + 1}'

    def is_bot(self, seat):
        return (self.mode == "pvp" and seat in self.ai_seats or
                self.mode == "pve" and not any(c["seat"] == seat for c in self.clients.values()))

    def is_takeover(self, seat):
        return any(c["seat"] == seat and c.get("takeover", False) for c in self.clients.values())

    def policy_controls(self, seat):
        return self.is_bot(seat) or self.is_takeover(seat) or self.mode != "pvp" and self.autoplay

    def invalidate_policy_control(self, seats):
        for seat in seats:
            self.policy_epochs[seat] += 1
        self.grace_policy_seen = {(revision, actor) for revision, actor in self.grace_policy_seen
                                  if actor not in seats}

    def set_takeover(self, token, enabled):
        """Delegate only the caller's occupied chair; keep ownership and private cards."""
        client = self.check(token)
        if type(enabled) is not bool:
            raise ValueError("enabled 须为布尔值")
        if client["seat"] is None:
            raise IllegalAction("SEAT_REQUIRED", "请先确认自己的座位")
        if not self.started:
            raise IllegalAction("LOBBY", "开局后可以随时开启 AI 托管")
        if client["takeover"] == enabled:
            return
        client["takeover"] = enabled
        self.invalidate_policy_control({client["seat"]})
        if not enabled and self.policy_error_actor == client["seat"]:
            self.policy_error = False
            self.policy_error_actor = None
        self.next_bot_at = time.monotonic() + 0.15
        self.refresh_pause(); self.bump()

    def set_autoplay(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("enabled 须为布尔值")
        if self.autoplay != enabled:
            self.autoplay = enabled
            self.invalidate_policy_control(set(range(4)))
            self.bump()

    def _pvp_seat(self, seat):
        if self.mode != "pvp":
            raise IllegalAction("PVP", "选座仅用于好友房间")
        if type(seat) is not int or seat not in range(4):
            raise ValueError("座位须为 0..3 的整数")

    def confirm_seat(self, token, seat):
        """Caller holds condition: availability and assignment are atomic."""
        client = self.check(token)
        self._pvp_seat(seat)
        if client["seat"] == seat:
            return
        if client["seat"] is not None:
            raise IllegalAction("SEAT_CONFIRMED", "请先取消原选座，再选择新位置")
        if not self.seat_selection_open():
            raise IllegalAction("IN_PROGRESS", "本局已开始，请在两局之间替换 AI")
        if any(c["seat"] == seat for c in self.clients.values()):
            raise IllegalAction("SEAT_TAKEN", "这个位置已被确认，请选择其他空席")
        self.ai_seats.discard(seat)
        self.departed_names.pop(seat, None)
        client["seat"] = seat
        self.invalidate_policy_control({seat})
        if self.started and (self.host is None or self.clients[self.host]["seat"] is None):
            self.elect_host()
        self.refresh_pause(); self.bump()

    def cancel_seat(self, token):
        client = self.check(token)
        if self.mode != "pvp":
            raise IllegalAction("PVP", "选座仅用于好友房间")
        if self.started:
            raise IllegalAction("SEATS_LOCKED", "开局后不能取消或更换座位，请使用离开房间")
        if client["seat"] is not None:
            self.invalidate_policy_control({client["seat"]})
            client["seat"] = None
            self.bump()

    def set_ai(self, token, seat, enabled):
        client = self.check(token)
        if self.mode != "pvp":
            raise IllegalAction("PVP", "AI 补位仅用于好友房间")
        if token != self.host:
            raise IllegalAction("HOST_ONLY", "请由房主设置 AI 席位")
        if type(enabled) is not bool:
            raise ValueError("enabled 须为布尔值")
        if seat is not None:
            self._pvp_seat(seat)
        elif not enabled:
            raise ValueError("移除 AI 时须指定座位")
        if enabled and client["seat"] is None:
            raise IllegalAction("SEAT_REQUIRED", "房主请先确认自己的座位，再添加 AI")
        if self.started and not enabled:
            raise IllegalAction("SEATS_LOCKED", "开局后不能移除 AI；真人主动离开的空席仍可补 AI")
        seats = ({seat} if seat is not None else set(range(4)) - self.occupied_seats())
        if enabled and any(c["seat"] in seats for c in self.clients.values()):
            raise IllegalAction("SEAT_TAKEN", "已有真人确认的座位不能替换为 AI")
        before = self.ai_seats.copy()
        if enabled:
            self.ai_seats.update(seats)
        else:
            self.ai_seats.difference_update(seats)
        if before != self.ai_seats:
            self.invalidate_policy_control(before ^ self.ai_seats)
            self.refresh_pause(); self.bump()
            self.next_bot_at = time.monotonic() + 0.4

    def start_room(self, token):
        client = self.check(token)
        if self.mode != "pvp":
            raise IllegalAction("PVP", "此操作仅用于好友房间")
        if token != self.host:
            raise IllegalAction("HOST_ONLY", "请由房主开始对局")
        if self.started:
            return
        if client["seat"] is None:
            raise IllegalAction("SEAT_REQUIRED", "房主请先确认自己的座位")
        if len(self.occupied_seats()) != 4:
            raise IllegalAction("SEATS_NOT_READY", "四个席位须全部确认，可用 AI 补齐空席")
        if any(c["seat"] is not None and time.monotonic() - c["seen"] >= DISCONNECT_SECONDS
               for c in self.clients.values()):
            raise IllegalAction("PLAYERS_OFFLINE", "有已确认的玩家离线，请等待重连")
        self.started = True
        self.refresh_pause(); self.bump()
        self.next_bot_at = time.monotonic() + 0.4

    def check(self, token):
        if token not in self.clients:
            raise IllegalAction("SESSION", "会话已失效，请重新进入")
        self.clients[token]["seen"] = time.monotonic()
        return self.clients[token]

    def refresh_pause(self):
        """Check connectivity under the room lock, including between bot ticks."""
        now = time.monotonic()
        paused = self.policy_error or (self.mode == "pvp" and self.started and
                 (len(self.occupied_seats()) < 4 or any(c["seat"] is not None and
                      not c.get("takeover", False) and now - c["seen"] >= DISCONNECT_SECONDS
                      for c in self.clients.values())))
        if paused != self.paused:
            self.paused = paused
            self.sync_bidding_window(now)
            self.bump()

    def snapshot(self, token):
        client = self.check(token)
        self.refresh_pause()
        self.close_bidding_if_due()
        players = []
        for seat in range(4):
            human = next((c for c in self.clients.values() if c["seat"] == seat), None)
            bot = self.is_bot(seat)
            players.append({"seat": seat, "name": self.player_name(seat, human, bot),
                            "occupied": human is not None or bot or self.mode == "self",
                            "online": time.monotonic() - human["seen"] < DISCONNECT_SECONDS if human else bot or self.mode == "self",
                            "bot": bot, "takeover": bool(human and human.get("takeover", False))})
        observer = self.env.current_player if self.mode == "self" else client["seat"]
        obs = self.env.observe(observer, omniscient=self.mode == "self") if self.started and observer is not None else None
        if obs is not None:
            obs['trick_review_seconds'] = self.trick_review_remaining()
            obs['bidding_window'] = {'complete': self.bidding_deadline is not None,
                'remaining_seconds': max(0, self.bidding_deadline - (self.bidding_paused_at if self.bidding_paused_at is not None else time.monotonic()))
                    if self.bidding_deadline is not None else 0,
                'duration_seconds': BIDDING_GRACE_SECONDS}
        return {"version": self.version, "room": self.code, "mode": self.mode,
                "seat": client["seat"], "name": client["name"], "host": token == self.host, "players": players,
                "controlled": list(range(4)) if self.mode == "self" else [client["seat"]] if client["seat"] is not None else [],
                "members": [{"name": c["name"], "seat": c["seat"], "self": t == token,
                             "online": time.monotonic() - c["seen"] < DISCONNECT_SECONDS}
                            for t, c in self.clients.items()],
                "ai_seats": sorted(self.ai_seats),
                "can_start": self.mode == "pvp" and not self.started and token == self.host and
                    client["seat"] is not None and all(p["occupied"] and p["online"] for p in players),
                "started": self.started, "paused": self.paused, "autoplay": self.autoplay,
                "seat_selection_open": self.mode == "pvp" and self.seat_selection_open(),
                "between_rounds": self.started and self.env.phase == "round_end",
                "takeover": client.get("takeover", False),
                "ai": {"id": self.ai_id, "name": self.ai_name} if self.mode == "pve" or self.ai_seats or
                    any(c.get("takeover", False) for c in self.clients.values()) else None,
                "bot_thinking": self.pending is not None,
                "replay_id": self.replay_id, "history_error": self.history_error,
                "settlement": self.settlement(client),
                "record_plays": self.record_plays, "can_set_record_plays": token == self.host,
                "pause_reason": "policy_error" if self.policy_error else "disconnected" if self.paused else None,
                "config": asdict(self.config), "state": obs}

    def can_act(self, token, action):
        client = self.check(token)
        self.refresh_pause()
        if not isinstance(action, dict):
            raise IllegalAction("ACTION", "动作须为 JSON 对象")
        if not self.started:
            raise IllegalAction("LOBBY", "等待选座完成，由房主开始对局")
        if self.mode == "pvp" and client["seat"] is None:
            raise IllegalAction("SEAT_REQUIRED", "请先选择空席并确认座位")
        if self.paused:
            raise IllegalAction("PAUSED", "自动策略异常，请检查服务端日志" if self.policy_error else "玩家离线，等待重新连接")
        self.sync_bidding_window()
        if self.bidding_deadline is not None and action.get('type') in ('pass','random'):
            raise IllegalAction('BIDDING_WAIT', '牌已摸完，等待六秒实时亮主结束；无需继续摸牌')
        if action.get("type") == "next_round":
            if token != self.host:
                raise IllegalAction("HOST_ONLY", "下一局须由房主开始")
            self._ensure_review()
            if self.env.phase in ("round_end", "match_end") and self.review_confirmed_by is None:
                raise IllegalAction("REVIEW_PENDING", "请先由本局庄家确认底牌和计分")
            return self.env.current_player
        if client.get("takeover", False):
            raise IllegalAction("AI_CONTROLLED", "AI 正在托管你的座位，请先取消 AI 托管再手动行动")
        if self.mode != 'self' and self.env.phase == 'dealing' and action.get('type') == 'bid':
            return client['seat']
        if self.mode != "self" and client["seat"] != self.env.current_player:
            raise IllegalAction("NOT_YOUR_TURN", "还没轮到你")
        return self.env.current_player


class Registry:
    def __init__(self, policy_factory=None, max_rooms=32, idle_seconds=ROOM_IDLE_SECONDS, history_dir=None):
        self.policy_factory = policy_factory
        self.history = HistoryStore(history_dir or ROOT / "runtime" / "history")
        self.rooms = {}
        self.tokens = {}
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.frozen = threading.Event()
        self.frozen_at = None
        self.max_rooms = max_rooms
        self.idle_seconds = idle_seconds

    def prune(self):
        """Only expire rooms whose entire membership has stopped polling."""
        now = time.monotonic()
        with self.lock:
            if self.frozen.is_set():
                return
            for code, room in list(self.rooms.items()):
                with room.condition:
                    expired = (now - max(c["seen"] for c in room.clients.values()) > self.idle_seconds
                               if room.clients else now - room.updated >
                               (self.idle_seconds if room.mode == 'pvp' else 60))
                    if expired:
                        for token in room.clients:
                            self.tokens.pop(token, None)
                        del self.rooms[code]

    def enter(self, data):
        mode = data.get("mode", "self")
        if mode not in ("self", "pve", "pvp"):
            raise ValueError("mode 须为 self / pve / pvp")
        name = str(data.get("name", "玩家")).strip() or "玩家"
        config = GameConfig(**data.get("config", {}))
        self.prune()
        with self.lock:
            if mode == "pvp":
                code = str(data.get("room", "")).strip()
                if not code:
                    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
                    code = "".join(secrets.choice(alphabet) for _ in range(8))
                    while code in self.rooms:
                        code = "".join(secrets.choice(alphabet) for _ in range(8))
                if not re.fullmatch(r"[A-Za-z0-9_-]{3,24}", code):
                    raise ValueError("房间号为 3..24 位字母、数字、下划线或短横线")
                room = self.rooms.get(code)
                if room is None:
                    if len(self.rooms) >= self.max_rooms:
                        raise IllegalAction("ROOM_LIMIT", "牌桌已满，请稍后再创建房间")
                    room = Room(code, mode, config, data.get("seed"), self.policy_factory, self.history); self.rooms[code] = room
                elif room.mode != "pvp":
                    raise ValueError("该房间号不可用")
                token = room.join(name)
            else:
                if len(self.rooms) >= self.max_rooms:
                    raise IllegalAction("ROOM_LIMIT", "牌桌已满，请稍后再创建房间")
                code = secrets.token_hex(6)
                seat = data.get("seat", 0)
                if type(seat) is not int or seat not in range(4):
                    raise ValueError("玩家座位须为 0..3")
                room = Room(code, mode, config, data.get("seed"), self.policy_factory, self.history); self.rooms[code] = room
                token = room.join(name, seat)
            self.tokens[token] = room
            with room.condition:
                return {"token": token, **room.snapshot(token)}

    def lookup(self, token):
        with self.lock:
            room = self.tokens.get(token)
        if room is None:
            raise IllegalAction("SESSION", "请先进入对局")
        return room

    def leave(self, token):
        # Registry -> room is also the ordering used by admission and pruning.
        # Retried leave requests are harmless after the first response is lost.
        with self.lock:
            room = self.tokens.get(token)
            if room is not None:
                with room.condition:
                    if token in room.clients:
                        room.leave(token)
                    self.tokens.pop(token, None)

    def run_bots(self):
        # Policy computations never hold a room lock. Keep at most one task per
        # room and validate its revision before applying an asynchronous result.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="tractor-ai") as pool:
            self._dispatch_bots(pool)

    def freeze(self, timeout=8):
        """Gateway has drained HTTP; let private policy RNG mutations finish."""
        from .checkpoint import dump_registry
        with self.lock:
            if self.frozen_at is None:
                self.frozen_at = time.monotonic()
                self.frozen.set()
            rooms = list(self.rooms.values())
        deadline = time.monotonic() + timeout
        for room in rooms:
            with room.condition:
                pending = room.pending
            if pending:
                future = pending[0]
                future.cancel()  # Cancel queued work; running RNG must drain.
                # Never serialize a running Future or apply its stale result.
                try:
                    future.result(timeout=max(0, deadline - time.monotonic()))
                except TimeoutError:
                    raise RuntimeError('AI 排空超时，取消更新')
                except Exception:
                    pass  # A failed result is retried by the restored worker.
                with room.condition:
                    room.pending = None
        return dump_registry(self)

    def resume(self):
        from .checkpoint import shift_clocks
        with self.lock:
            if self.frozen_at is None:
                return
            duration = time.monotonic() - self.frozen_at
            for room in self.rooms.values():
                with room.condition:
                    shift_clocks(room, duration)
                    room.condition.notify_all()
            self.frozen_at = None
            self.frozen.clear()

    def _dispatch_bots(self, pool):
        while not self.stopped.wait(0.05):
            with self.lock:
                rooms = list(self.rooms.values())
            now = time.monotonic()
            for room in rooms:
                with room.condition:
                    if self.frozen.is_set():
                        continue
                    room.refresh_pause()
                    room_time = time.monotonic()
                    room.close_bidding_if_due(room_time)
                    if room.pending is not None:
                        future, revision, actor, epoch = room.pending
                        if not future.done():
                            continue
                        room.pending = None
                        eligible = room.policy_controls(actor) and room.policy_epochs[actor] == epoch and (
                            any(c['seat'] is not None for c in room.clients.values()) or
                            room.mode == 'pvp' and len(room.ai_seats) == 4)
                        if eligible and room.started and not room.paused and room.env.revision == revision:
                            try:
                                action = future.result()
                                if room.bidding_deadline is not None and action.get('type') == 'pass':
                                    room.grace_policy_seen.add((revision, actor))
                                elif room.env.current_player == actor or room.env.phase == 'dealing' and action.get('type') == 'bid':
                                    room.step(action, actor, now=room_time)
                                    room.next_bot_at = time.monotonic() + (0.12 if room.env.phase == "dealing" else 0.35)
                            except Exception as exc:
                                room.autoplay = False
                                room.policy_error = True
                                room.policy_error_actor = actor
                                room.paused = True
                                room.env._event("server_error", message="自动策略暂停，请检查服务端日志")
                                print(f"Bot error in {room.code}: {type(exc).__name__}: {exc}", flush=True)
                        room.bump()
                    if not room.clients and (room.mode != 'pvp' or len(room.ai_seats) != 4):
                        continue
                    if room.started and room.env.phase in ("round_end", "match_end"):
                        if room.finish_ai_review():
                            room.bump()
                        continue
                    if not room.started or room.paused or room.env.phase in ("round_end", "match_end"):
                        continue
                    if now < room.next_bot_at:
                        continue
                    if room.trick_review_remaining(room_time):
                        continue
                    actor = self._policy_actor(room)
                    if actor is not None:
                        observation = room.env.observe(actor)
                        future = pool.submit(room.policies[actor].act, observation)
                        room.pending = (future, room.env.revision, actor, room.policy_epochs[actor])
                        room.bump()
            self.prune()

    @staticmethod
    def _policy_actor(room):
        human_seats = {c["seat"] for c in room.clients.values() if c["seat"] is not None}
        if (not human_seats and not (room.mode == 'pvp' and len(room.ai_seats) == 4) or
                not room.started or room.paused or room.env.phase in ('round_end','match_end')):
            return None
        def eligible(actor):
            return room.policy_controls(actor)
        if room.bidding_deadline is not None:
            for offset in range(4):
                actor = room.env.next_seat(room.env.current_player, offset)
                if (eligible(actor) and (room.env.revision, actor) not in room.grace_policy_seen
                        and room.env.bid_options(actor)):
                    return actor
            return None
        return room.env.current_player if eligible(room.env.current_player) else None

    @staticmethod
    def _should_act(room):
        return Registry._policy_actor(room) is not None


class Handler(BaseHTTPRequestHandler):
    server_version = "TractorSimulator/0.1"

    def setup(self):
        self.request.settimeout(20)
        super().setup()

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def send_bytes(self, body, content_type, status=200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            pass

    def json_response(self, data, status=200):
        self.send_bytes(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def token(self):
        header = self.headers.get("Authorization", "")
        return header[7:] if header.startswith("Bearer ") else ""

    def do_GET(self):
        parsed = urlparse(self.path)
        if self.server.registry.frozen.is_set() and parsed.path != '/api/health':
            self.json_response({'error': 'UPDATING', 'message': '更新中，请稍候'}, 503); return
        try:
            if parsed.path == "/api/history":
                self.json_response(self.server.registry.history.listing()); return
            if parsed.path.startswith("/api/history/"):
                try:
                    record = self.server.registry.history.read(parsed.path.removeprefix("/api/history/"))
                except KeyError:
                    self.json_response({"error": "NOT_FOUND", "message": "这份回放不存在"}, 404); return
                self.json_response(record); return
            if parsed.path == "/api/state":
                token = self.token(); room = self.server.registry.lookup(token)
                q = parse_qs(parsed.query)
                since = int(q.get("since", [-1])[0])
                wait = min(5, max(0, float(q.get("wait", [0])[0])))
                with room.condition:
                    room.check(token)
                    if since == room.version and wait:
                        room.condition.wait(wait)
                    snapshot = room.snapshot(token)
                self.json_response(snapshot)
                return
            if parsed.path == "/api/site":
                self.json_response({"public_url": self.server.site_public_url(),
                                    "disconnect_seconds": DISCONNECT_SECONDS,
                                    "room_idle_seconds": self.server.registry.idle_seconds}); return
            if parsed.path == "/api/health":
                self.json_response({"ok": True, "schema_version": 1, "service": "tractor_sim",
                                    "bind_host": self.server.server_address[0],
                                    "pve_ai": AI_ID,
                                    "instance_id": self.server.instance_id}); return
            if parsed.path == "/rules":
                rules = ROOT.parent / "双升游戏规则.md"
                if not rules.is_file():
                    rules = ROOT / "rules" / "双升游戏规则.md"
                self.send_bytes(rules.read_bytes(), "text/plain; charset=utf-8"); return
            relative = unquote(parsed.path).lstrip("/") or "index.html"
            target = (WEB / relative).resolve()
            if not target.is_relative_to(WEB.resolve()) or not target.is_file():
                self.json_response({"error": "NOT_FOUND"}, 404); return
            content_type = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}.get(target.suffix, "application/octet-stream")
            self.send_bytes(target.read_bytes(), content_type + "; charset=utf-8")
        except IllegalAction as exc:
            self.json_response(exc.as_dict(), 403)
        except (ValueError, OSError) as exc:
            self.json_response({"error": "REQUEST", "message": str(exc)}, 400)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if path.startswith('/api/internal/'):
            if (not self.server.control_key or self.client_address[0] != '127.0.0.1' or
                    not secrets.compare_digest(self.headers.get('X-Tractor-Control', ''), self.server.control_key)):
                self.json_response({'error': 'NOT_FOUND'}, 404); return
            try:
                if path == '/api/internal/freeze':
                    self.json_response(self.server.registry.freeze())
                elif path == '/api/internal/pause':
                    registry = self.server.registry
                    with registry.lock:
                        if registry.frozen_at is None:
                            registry.frozen_at = time.monotonic()
                            registry.frozen.set()
                        self.json_response({'schema': 1, 'clock': registry.frozen_at, 'rooms': [],
                            'max_rooms': registry.max_rooms, 'idle_seconds': registry.idle_seconds})
                elif path == '/api/internal/resume':
                    self.server.registry.resume()
                    self.json_response({'ok': True})
                else:
                    self.json_response({'error': 'NOT_FOUND'}, 404)
            except Exception as exc:
                self.json_response({'error': 'UPDATE_FAILED', 'message': str(exc)}, 500)
            return
        if self.server.registry.frozen.is_set():
            self.json_response({'error': 'UPDATING', 'message': '更新中，请稍候'}, 503); return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json":
                raise ValueError("请求须使用 application/json")
            if not 0 < size <= 65536:
                raise ValueError("请求体须为 1..65536 字节")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("请求体须为 JSON 对象")
            path = urlparse(self.path).path
            if path == "/api/session":
                self.json_response(self.server.registry.enter(data)); return
            token = self.token()
            if path == "/api/leave":
                self.server.registry.leave(token)
                self.json_response({"ok": True}); return
            room = self.server.registry.lookup(token)
            with room.condition:
                room.check(token)
                room.refresh_pause()
                action_time = time.monotonic()
                room.close_bidding_if_due(action_time)
                if path == "/api/validate":
                    action = data.get('action', {})
                    realtime_bid = isinstance(action, dict) and action.get('type') == 'bid' and room.env.phase == 'dealing'
                    if realtime_bid and (data.get('round') != room.env.round_no or data.get('redeals') != room.env.redeals):
                        self.json_response({'error': 'STALE', 'message': '摸牌局已变化，请重新选择亮主牌'}, 409); return
                    if not realtime_bid and 'version' in data and data['version'] != room.version:
                        self.json_response({'error': 'STALE', 'message': '牌桌已更新，正在同步本局级牌和领出'}, 409); return
                    context = data.get('context')
                    if context is not None:
                        if not isinstance(context, dict) or type(context.get('level')) is not int or context.get('trump') not in (None,'S','H','D','C'):
                            raise ValueError('context 须包含整数 level 和主花色 trump')
                        context = {'level':context['level'], 'trump':context.get('trump')}
                        if context['level'] != room.env.level or not realtime_bid and context['trump'] != room.env.trump:
                            self.json_response({'error':'STALE', 'message':'页面级牌或主花色与裁判不同，正在同步牌桌'}, 409); return
                    player = room.can_act(token, action)
                    response = room.env.validate_action(action, player)
                    response.update(version=room.version, level=room.env.level, trump=room.env.trump,
                                    round=room.env.round_no, redeals=room.env.redeals, phase=room.env.phase)
                    room.record_validation_error(player, action, response, context)
                elif path == "/api/seat/confirm":
                    room.confirm_seat(token, data.get("seat"))
                    response = room.snapshot(token)
                elif path == "/api/seat/cancel":
                    room.cancel_seat(token)
                    response = room.snapshot(token)
                elif path == "/api/room/ai":
                    room.set_ai(token, data.get("seat"), data.get("enabled"))
                    response = room.snapshot(token)
                elif path == "/api/room/start":
                    room.start_room(token)
                    response = room.snapshot(token)
                elif path == '/api/room/record-plays':
                    room.set_record_plays(token, data.get('enabled'))
                    response = room.snapshot(token)
                elif path in ('/api/action', '/api/bid'):
                    realtime_bid = path == '/api/bid'
                    if realtime_bid:
                        if room.env.phase != 'dealing':
                            raise IllegalAction('BIDDING_CLOSED', '实时亮主仅限摸牌阶段，摸牌已结束；请按轮询行动')
                        if data.get('round') != room.env.round_no or data.get('redeals') != room.env.redeals:
                            self.json_response({'error': 'STALE', 'message': '摸牌局已变化，请重新选择亮主牌'}, 409); return
                        if not isinstance(data.get('action'), dict) or data['action'].get('type') != 'bid':
                            raise IllegalAction('BID_REQUIRED', '此接口只能提交摸牌阶段的亮主／反主')
                    elif data.get("version") != room.version:
                        self.json_response({"error": "STALE", "message": "对局已更新，请重试"}, 409); return
                    action = data.get("action", {})
                    player = room.can_act(token, action)
                    if action.get("type") == "random":
                        if room.mode == "pvp":
                            raise IllegalAction("PVP", "PVP 请自行选择动作")
                        action = room.policy.act(room.env.observe(player))
                    try:
                        room.step(action, player, now=action_time)
                    except IllegalAction as exc:
                        room.record_validation_error(player, action, exc.as_dict())
                        if realtime_bid:
                            exc.message = '亮主无效：' + exc.message
                        raise
                    room.bump()
                    room.next_bot_at = time.monotonic() + 0.55
                    response = room.snapshot(token)
                elif path == "/api/confirm-result":
                    if 'round' not in data and data.get("version") != room.version:
                        self.json_response({"error": "STALE", "message": "对局已更新，请重试"}, 409); return
                    if 'round' in data and type(data['round']) is not int:
                        raise ValueError('round 须为整数')
                    room.confirm_result(token, data.get('round'))
                    response = room.snapshot(token)
                elif path == "/api/autoplay":
                    if room.mode == "pvp":
                        room.set_takeover(token, data.get("enabled"))
                    else:
                        room.set_autoplay(data.get("enabled"))
                    response = room.snapshot(token)
                else:
                    self.json_response({"error": "NOT_FOUND"}, 404); return
            self.json_response(response)
        except IllegalAction as exc:
            self.json_response(exc.as_dict(), 422)
        except (ValueError, TypeError, KeyError) as exc:
            self.json_response({"error": "REQUEST", "message": str(exc)}, 400)
        except Exception as exc:
            print(f"Request error: {type(exc).__name__}: {exc}", flush=True)
            self.json_response({"error": "INTERNAL", "message": "服务端发生错误，请查看日志"}, 500)


class TractorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def server_bind(self):
        # Windows SO_REUSEADDR can let two live servers own the same address.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, verbose=False, policy_factory=None, max_rooms=32, public_url=None, history_dir=None,
                 public_entry_file=None, control_key=None, checkpoint=None, instance_id=None):
        self.public_url = public_url
        self.public_entry_file = Path(public_entry_file) if public_entry_file else ROOT / 'runtime' / 'public-entry.json'
        self.instance_id = instance_id or secrets.token_hex(16)
        self.control_key = control_key
        self.request_slots = threading.BoundedSemaphore(160)
        super().__init__(address, Handler)
        self.verbose = verbose
        self.registry = Registry(policy_factory, max_rooms=max_rooms, history_dir=history_dir)
        if checkpoint is not None:
            from .checkpoint import load_registry
            load_registry(self.registry, checkpoint)
        self.bot_thread = threading.Thread(target=self.registry.run_bots, daemon=True, name="tractor-policies")
        self.bot_thread.start()

    def site_public_url(self):
        if self.public_url:
            return self.public_url
        try:
            # A tunnel process can restart independently of the game process.
            # Never reuse an entry validated against an earlier game instance.
            with self.public_entry_file.open(encoding='utf-8') as handle:
                entry = json.loads(handle.read(4096))
            if not isinstance(entry, dict) or entry.get('instance_id') != self.instance_id:
                return None
            from .internet import valid_public_url
            return valid_public_url(entry.get('public_url', ''))
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()

    def server_close(self):
        if hasattr(self, "registry"):
            self.registry.stopped.set()
            self.bot_thread.join(timeout=4)
        super().server_close()
