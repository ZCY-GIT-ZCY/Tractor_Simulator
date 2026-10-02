"""Standard-library room server. Server owns state and redacts every response.

Clients use authenticated long polling and optimistic revisions. Four-player
rooms pause on disconnect; a reconnect retains the original seat and hand.
"""
import json
import re
import secrets
import socket
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from .engine import TractorEnv, GameConfig
from .policy import RandomPolicy
from .rules import IllegalAction

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DISCONNECT_SECONDS = 25
ROOM_IDLE_SECONDS = 2 * 60 * 60


class Room:
    def __init__(self, code, mode, config, seed=None, policy_factory=RandomPolicy):
        self.code, self.mode, self.config = code, mode, config
        self.env = TractorEnv(config, seed)
        self.policy = policy_factory(seed)
        self.clients = {}
        self.started = mode != "pvp"
        self.host = None
        self.autoplay = False
        self.paused = False
        self.policy_error = False
        self.version = 0
        self.updated = time.monotonic()
        self.next_bot_at = time.monotonic() + 0.4
        self.condition = threading.Condition(threading.RLock())

    def bump(self):
        self.version += 1
        self.updated = time.monotonic()
        self.condition.notify_all()

    def join(self, name, seat=None):
        with self.condition:
            occupied = {c["seat"] for c in self.clients.values()}
            if seat is None:
                seat = next((s for s in range(4) if s not in occupied), None)
            if seat is None or seat in occupied:
                raise IllegalAction("ROOM_FULL", "房间已满，请换房间号")
            token = secrets.token_urlsafe(32)
            self.clients[token] = {"seat": seat, "name": name[:24], "seen": time.monotonic()}
            if self.host is None:
                self.host = token
            if self.mode == "pvp" and len(self.clients) == 4:
                self.started = True
            self.refresh_pause()
            self.bump()
            return token

    def check(self, token):
        if token not in self.clients:
            raise IllegalAction("SESSION", "会话已失效，请重新进入")
        self.clients[token]["seen"] = time.monotonic()
        return self.clients[token]

    def refresh_pause(self):
        """Check connectivity under the room lock, including between bot ticks."""
        now = time.monotonic()
        paused = self.policy_error or (self.mode == "pvp" and self.started and
                 (len(self.clients) < 4 or any(now - c["seen"] >= DISCONNECT_SECONDS
                                             for c in self.clients.values())))
        if paused != self.paused:
            self.paused = paused
            self.bump()

    def snapshot(self, token):
        client = self.check(token)
        self.refresh_pause()
        players = []
        for seat in range(4):
            human = next((c for c in self.clients.values() if c["seat"] == seat), None)
            bot = self.mode == "pve" and seat != client["seat"]
            players.append({"seat": seat, "name": human["name"] if human else f"随机 AI {seat + 1}" if bot else f"座位 {seat + 1}",
                            "occupied": human is not None or bot or self.mode == "self",
                            "online": time.monotonic() - human["seen"] < DISCONNECT_SECONDS if human else bot or self.mode == "self",
                            "bot": bot})
        observer = self.env.current_player if self.mode == "self" else client["seat"]
        obs = self.env.observe(observer, omniscient=self.mode == "self") if self.started else None
        return {"version": self.version, "room": self.code, "mode": self.mode,
                "seat": client["seat"], "host": token == self.host, "players": players,
                "controlled": list(range(4)) if self.mode == "self" else [client["seat"]],
                "started": self.started, "paused": self.paused, "autoplay": self.autoplay,
                "pause_reason": "policy_error" if self.policy_error else "disconnected" if self.paused else None,
                "config": asdict(self.config), "state": obs}

    def can_act(self, token, action):
        client = self.check(token)
        self.refresh_pause()
        if not isinstance(action, dict):
            raise IllegalAction("ACTION", "动作须为 JSON 对象")
        if not self.started:
            raise IllegalAction("LOBBY", "等待四位玩家入座")
        if self.paused:
            raise IllegalAction("PAUSED", "自动策略异常，请检查服务端日志" if self.policy_error else "玩家离线，等待重新连接")
        if action.get("type") == "next_round":
            if token != self.host:
                raise IllegalAction("HOST_ONLY", "下一局须由房主开始")
            return self.env.current_player
        if self.mode != "self" and client["seat"] != self.env.current_player:
            raise IllegalAction("NOT_YOUR_TURN", "还没轮到你")
        return self.env.current_player


class Registry:
    def __init__(self, policy_factory=RandomPolicy, max_rooms=32, idle_seconds=ROOM_IDLE_SECONDS):
        self.policy_factory = policy_factory
        self.rooms = {}
        self.tokens = {}
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.max_rooms = max_rooms
        self.idle_seconds = idle_seconds

    def prune(self):
        """Only expire rooms whose entire membership has stopped polling."""
        now = time.monotonic()
        with self.lock:
            for code, room in list(self.rooms.items()):
                with room.condition:
                    expired = (now - max(c["seen"] for c in room.clients.values()) > self.idle_seconds
                               if room.clients else now - room.updated > 60)
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
                    room = Room(code, mode, config, data.get("seed"), self.policy_factory); self.rooms[code] = room
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
                room = Room(code, mode, config, data.get("seed"), self.policy_factory); self.rooms[code] = room
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

    def run_bots(self):
        while not self.stopped.wait(0.05):
            with self.lock:
                rooms = list(self.rooms.values())
            now = time.monotonic()
            for room in rooms:
                with room.condition:
                    if not room.clients:
                        continue
                    room.refresh_pause()
                    if not room.started or room.paused or room.env.phase in ("round_end", "match_end"):
                        continue
                    if now < room.next_bot_at:
                        continue
                    human_seats = {c["seat"] for c in room.clients.values()}
                    should_act = room.mode != "pvp" and (room.autoplay or
                                 (room.mode == "pve" and room.env.current_player not in human_seats))
                    if should_act:
                        try:
                            room.env.step(room.policy.act(room.env.observe(room.env.current_player)))
                            room.next_bot_at = now + (0.12 if room.env.phase == "dealing" else 0.65)
                            room.bump()
                        except Exception as exc:
                            room.autoplay = False
                            room.policy_error = True
                            room.paused = True
                            room.env._event("server_error", message="自动策略暂停，请检查服务端日志")
                            room.bump()
                            print(f"Bot error in {room.code}: {exc}", flush=True)
            self.prune()


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
        try:
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
                self.json_response({"public_url": self.server.public_url,
                                    "disconnect_seconds": DISCONNECT_SECONDS,
                                    "room_idle_seconds": self.server.registry.idle_seconds}); return
            if parsed.path == "/api/health":
                self.json_response({"ok": True, "schema_version": 1, "service": "tractor_sim",
                                    "bind_host": self.server.server_address[0],
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
            token = self.token(); room = self.server.registry.lookup(token)
            if path == "/api/leave":
                with room.condition:
                    room.check(token)
                    del room.clients[token]
                    if token == room.host:
                        room.host = next(iter(room.clients), None)
                    room.refresh_pause()
                    room.bump()
                with self.server.registry.lock:
                    self.server.registry.tokens.pop(token, None)
                self.json_response({"ok": True}); return
            with room.condition:
                room.check(token)
                room.refresh_pause()
                if path == "/api/validate":
                    action = data.get("action", {})
                    player = room.can_act(token, action)
                    response = room.env.validate_action(action, player)
                elif path == "/api/action":
                    if data.get("version") != room.version:
                        self.json_response({"error": "STALE", "message": "对局已更新，请重试"}, 409); return
                    action = data.get("action", {})
                    player = room.can_act(token, action)
                    if action.get("type") == "random":
                        if room.mode == "pvp":
                            raise IllegalAction("PVP", "PVP 请自行选择动作")
                        action = room.policy.act(room.env.observe(player))
                    room.env.step(action, player); room.bump()
                    room.next_bot_at = time.monotonic() + 0.55
                    response = room.snapshot(token)
                elif path == "/api/autoplay":
                    if room.mode == "pvp":
                        raise IllegalAction("PVP", "PVP 不允许启动随机托管")
                    if type(data.get("enabled")) is not bool:
                        raise ValueError("enabled 须为布尔值")
                    room.autoplay = data["enabled"]; room.bump()
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

    def __init__(self, address, verbose=False, policy_factory=RandomPolicy, max_rooms=32, public_url=None):
        self.public_url = public_url
        self.instance_id = secrets.token_hex(16)
        self.request_slots = threading.BoundedSemaphore(160)
        super().__init__(address, Handler)
        self.verbose = verbose
        self.registry = Registry(policy_factory, max_rooms=max_rooms)
        self.bot_thread = threading.Thread(target=self.registry.run_bots, daemon=True, name="tractor-policies")
        self.bot_thread.start()

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
        super().server_close()
