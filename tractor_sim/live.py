"""Stable HTTP gateway and transactional application updates, stdlib only.

No public administration routes. Operators submit owner-only local files.
Application workers run from immutable releases and bind loopback exclusively.
"""
import argparse
import hashlib
import http.client
import json
import os
import py_compile
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .checkpoint import private_json
from .server import ROOM_IDLE_SECONDS

PROTOCOL = 1
UPDATE_TIMEOUT = 35
ROOT = Path(__file__).resolve().parent.parent


def stage_release(source, operations):
    """Copy code + web + rules, excluding runtime, credentials and dependencies."""
    source, operations = Path(source).resolve(), Path(operations).resolve()
    files = []
    for folder in ('tractor_sim', 'web', 'rules', 'licenses'):
        for path in (source / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                files.append(path)
    for name in ('pyproject.toml', 'README.md'):
        if (source / name).is_file():
            files.append(source / name)
    if not (source / 'tractor_sim' / 'worker.py').is_file() or not (source / 'web' / 'index.html').is_file():
        raise ValueError('不是支持滚动更新的完整应用目录')
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
    release_id = digest.hexdigest()
    directory = operations / 'releases' / release_id
    if directory.is_dir():
        return directory
    temporary = operations / 'releases' / ('.staging-' + uuid.uuid4().hex)
    temporary.mkdir(parents=True, mode=0o700)
    try:
        for path in files:
            target = temporary / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        for path in (temporary / 'tractor_sim').rglob('*.py'):
            py_compile.compile(str(path), doraise=True)
        os.replace(temporary, directory)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return directory


class Worker:
    def __init__(self, release, operations, checkpoint, instance_id, history, public_entry, timeout=12):
        self.release = Path(release)
        self.key = secrets.token_hex(32)
        self.tag = uuid.uuid4().hex
        self.config = operations / ('worker-' + self.tag + '.json')
        self.ready = operations / ('ready-' + self.tag + '.json')
        private_json(self.config, {'ready': str(self.ready), 'checkpoint': str(checkpoint),
                                  'history': str(history), 'public_entry': str(public_entry),
                                  'instance_id': instance_id})
        env = dict(os.environ, TRACTOR_CONTROL_KEY=self.key, TRACTOR_WORKER_CONFIG=str(self.config),
                   PYTHONUTF8='1', PYTHONPATH=str(self.release))
        log = operations / 'workers.log'
        if log.is_file() and log.stat().st_size > 5 * 1024 * 1024:
            # Linux can rename an open log; Windows may deny it while the old
            # worker owns the handle. Logging must not prevent an update.
            try:
                log.replace(operations / 'workers.log.1')
            except OSError:
                pass
        # No shell, no user-supplied command; works on Windows and Linux.
        with log.open('ab') as output:
            self.process = subprocess.Popen([sys.executable, '-u', '-m', 'tractor_sim.worker'],
                cwd=self.release, env=env, stdout=output, stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            deadline = time.monotonic() + timeout
            while not self.ready.exists():
                if self.process.poll() is not None:
                    raise RuntimeError('新版 worker 启动失败，请检查 workers.log')
                if time.monotonic() >= deadline:
                    raise TimeoutError('新版 worker 启动超时')
                time.sleep(0.05)
            self.port = json.loads(self.ready.read_text(encoding='utf-8'))['port']
            # TCP readiness and frozen checkpoint restoration are both required.
            conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
            try:
                conn.request('GET', '/api/health')
                response = conn.getresponse()
                health = json.loads(response.read())
                if response.status != 200 or health.get('instance_id') != instance_id:
                    raise RuntimeError('新版 worker 健康检查失败')
            finally:
                conn.close()
        except BaseException:
            self.stop()
            raise

    def control(self, action, timeout=12):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        try:
            conn.request('POST', '/api/internal/' + action, body=b'',
                         headers={'X-Tractor-Control': self.key, 'Content-Length': '0'})
            response = conn.getresponse()
            data = json.loads(response.read())
            if response.status != 200:
                raise RuntimeError(data.get('message', 'worker 控制请求失败'))
            return data
        finally:
            conn.close()

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.config.unlink(missing_ok=True)
        self.ready.unlink(missing_ok=True)


class GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        path = unquote(urlparse(self.path).path)
        try:
            if self.headers.get('Transfer-Encoding'):
                self.reply(400, {'error': 'REQUEST'}); return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 65536:
                self.reply(413, {'error': 'REQUEST'}); return
            body = self.rfile.read(size) if size else None
            if body is not None and len(body) != size:
                self.reply(400, {'error': 'REQUEST'}); return
            # Consume a bounded POST body before closing a rejected request.
            # Otherwise Windows may reset the socket before the 404 is read.
            if path == '/api/health':
                health = self.server.health()
                self.reply(200 if health['ok'] else 503, health); return
            if path.startswith('/api/internal/'):
                self.reply(404, {'error': 'NOT_FOUND'}); return
            worker = self.server.acquire_worker()
            try:
                conn = http.client.HTTPConnection('127.0.0.1', worker.port, timeout=15)
                try:
                    headers = {k: v for k, v in self.headers.items()
                               if k.lower() not in ('host', 'connection', 'x-tractor-control',
                                                    'transfer-encoding', 'proxy-connection')}
                    conn.request(self.command, self.path, body=body, headers=headers)
                    response = conn.getresponse()
                    self.send_response(response.status)
                    for name, value in response.getheaders():
                        if name.lower() not in ('server', 'date', 'connection', 'transfer-encoding'):
                            self.send_header(name, value)
                    self.send_header('X-Tractor-Release', worker.release.name)
                    self.end_headers()
                    while chunk := response.read(65536):
                        self.wfile.write(chunk)
                finally:
                    conn.close()
            finally:
                self.server.release_worker()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (OSError, ValueError, TimeoutError) as exc:
            # Never retry a mutation: upstream may already have accepted it.
            try:
                self.reply(503, {'error': 'UPDATING', 'message': '连接暂时繁忙，请稍后重试'})
            except OSError:
                pass


class Gateway(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != 'nt'

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, source=ROOT, operations=None, history=None, public_entry=None):
        self.source = Path(source).resolve()
        self.operations = Path(operations or self.source / 'runtime' / 'live').resolve()
        self.operations.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != 'nt':
            self.operations.chmod(0o700)
        self.history = Path(history or self.source / 'runtime' / 'history').resolve()
        self.public_entry = Path(public_entry or self.source / 'runtime' / 'public-entry.json').resolve()
        self.instance_id = secrets.token_hex(16)
        self.condition = threading.Condition()
        self.update_lock = threading.Lock()
        self.updating = False
        self.active = 0
        self.stopping = threading.Event()
        self.slots = threading.BoundedSemaphore(160)
        self.worker = None
        super().__init__(address, GatewayHandler)
        try:
            release = stage_release(self.source, self.operations)
            initial = self.operations / 'initial.json'
            private_json(initial, {'schema': 1, 'clock': time.monotonic(), 'rooms': [],
                                   'max_rooms': 32, 'idle_seconds': ROOM_IDLE_SECONDS})
            self.worker = Worker(release, self.operations, initial, self.instance_id,
                                 self.history, self.public_entry)
            self.worker.control('resume')
            initial.unlink(missing_ok=True)
            self.requests = self.operations / 'requests'
            self.requests.mkdir(exist_ok=True, mode=0o700)
            private_json(self.operations / 'gateway.json', {'protocol': PROTOCOL, 'pid': os.getpid(),
                'port': self.server_port, 'instance_id': self.instance_id, 'release': release.name})
            self.watch_thread = threading.Thread(target=self.watch_updates, daemon=True)
            self.watch_thread.start()
        except BaseException:
            self.server_close()
            raise

    def health(self):
        with self.condition:
            return {'ok': self.worker is not None and self.worker.process.poll() is None,
                    'schema_version': 1, 'service': 'tractor_sim',
                    'bind_host': self.server_address[0], 'instance_id': self.instance_id,
                    'live_update': PROTOCOL, 'updating': self.updating,
                    'release': self.worker.release.name if self.worker else None}

    def acquire_worker(self):
        with self.condition:
            if not self.condition.wait_for(lambda: not self.updating or self.stopping.is_set(), UPDATE_TIMEOUT + 20):
                raise TimeoutError('等待更新超时')
            if self.stopping.is_set() or self.worker.process.poll() is not None:
                raise OSError('worker 已停止')
            self.active += 1
            return self.worker

    def release_worker(self):
        with self.condition:
            self.active -= 1
            self.condition.notify_all()

    def update(self, release, reset_rooms=False):
        release = Path(release).resolve()
        if release.parent != self.operations / 'releases' or not release.is_dir():
            raise ValueError('只允许已暂存的本地 release')
        with self.update_lock:
            old, candidate = self.worker, None
            if old.release == release and not reset_rooms:
                return {'ok': True, 'changed': False, 'release': release.name}
            snapshot = self.operations / ('checkpoint-' + uuid.uuid4().hex + '.json')
            committed = False
            deadline = time.monotonic() + UPDATE_TIMEOUT - 5
            def remaining(limit):
                value = min(limit, deadline - time.monotonic())
                if value <= 0:
                    raise TimeoutError('更新超时，保留旧 worker')
                return value
            try:
                with self.condition:
                    self.updating = True
                    if not self.condition.wait_for(lambda: self.active == 0, timeout=remaining(8)):
                        raise TimeoutError('请求排空超时，取消更新')
                private_json(snapshot, old.control('pause' if reset_rooms else 'freeze', timeout=remaining(12)))
                candidate = Worker(release, self.operations, snapshot, self.instance_id,
                                   self.history, self.public_entry, timeout=remaining(12))
                # Ready file and /health are produced only after all rooms restore.
                private_json(self.operations / 'gateway.json', {'protocol': PROTOCOL, 'pid': os.getpid(),
                    'port': self.server_port, 'instance_id': self.instance_id, 'release': release.name})
                candidate.control('resume', timeout=remaining(5))
                with self.condition:
                    self.worker = candidate
                    committed = True
                    self.updating = False
                    self.condition.notify_all()
            except BaseException:
                if candidate:
                    candidate.stop()
                old.control('resume', timeout=5)
                private_json(self.operations / 'gateway.json', {'protocol': PROTOCOL, 'pid': os.getpid(),
                    'port': self.server_port, 'instance_id': self.instance_id, 'release': old.release.name})
                raise
            finally:
                snapshot.unlink(missing_ok=True)
                with self.condition:
                    self.updating = False
                    self.condition.notify_all()
            # Promotion is final; cleanup failure must never roll back a live
            # candidate after it has already accepted player actions.
            if committed:
                old.stop()
            return {'ok': True, 'changed': True, 'release': release.name, 'rooms_reset': reset_rooms}

    def watch_updates(self):
        while not self.stopping.wait(0.2):
            for path in sorted(self.requests.glob('*.request.json')):
                result = path.with_name(path.name.replace('.request.json', '.result.json'))
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                    if data.get('instance_id') != self.instance_id:
                        raise ValueError('网关已重启，请重新提交更新')
                    reset_rooms = data.get('reset_rooms', False)
                    if type(reset_rooms) is not bool:
                        raise ValueError('reset_rooms 须为布尔值')
                    outcome = self.update(self.operations / 'releases' / data['release'], reset_rooms)
                except Exception as exc:
                    outcome = {'ok': False, 'message': str(exc)}
                private_json(result, outcome)
                path.unlink(missing_ok=True)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request); return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release(); raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_close(self):
        self.stopping.set()
        with self.condition:
            self.condition.notify_all()
        if hasattr(self, 'watch_thread'):
            self.watch_thread.join(timeout=40)
        if self.worker:
            self.worker.stop()
        super().server_close()


def main():
    parser = argparse.ArgumentParser(description='双升常驻网关：应用滚动更新保留牌局和入口')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--operations', type=Path)
    parser.add_argument('--history-dir', type=Path)
    args = parser.parse_args()
    gateway = Gateway((args.host, args.port), operations=args.operations, history=args.history_dir)
    print(f'Tractor live gateway: {args.host}:{gateway.server_port}', flush=True)
    try:
        gateway.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        gateway.server_close()


if __name__ == '__main__':
    main()
