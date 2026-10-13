"""Persistent outbound tunnel for an independently managed game server.

Restarting this process never restarts the game or discards rooms. A new entry
is published only after public HTTPS health reaches the current game instance.
"""
import argparse
import json
import signal
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .internet import RUNTIME, LauncherLease, Tunnel, check_public, install_cloudflared, valid_public_url


def origin_instance(origin):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(origin + '/api/health', timeout=3) as response:
            data = json.loads(response.read(4096))
        if (isinstance(data, dict) and data.get('service') == 'tractor_sim' and
                data.get('ok') is True and isinstance(data.get('instance_id'), str)):
            return data['instance_id']
    except (OSError, ValueError):
        pass
    return None


def validate_origin(origin):
    parsed = urlparse(origin)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or
            parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('origin 须为本机 HTTP 入口，例如 http://127.0.0.1:8765')
    if parsed.port is None or not 1 <= parsed.port <= 65535:
        raise ValueError('origin 须指定 1..65535 的端口')
    return origin.rstrip('/')


def publish_entry(runtime, url, instance):
    url = valid_public_url(url)
    # Atomic replacement makes concurrent /api/site reads safe.
    for name, content in (
        ('public-entry.json', json.dumps({'public_url': url, 'instance_id': instance})),
        ('public-url.txt', url + '\n'),
    ):
        temporary = runtime / (name + '.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(runtime / name)


def clear_entry(runtime):
    for name in ('public-entry.json', 'public-url.txt'):
        (runtime / name).unlink(missing_ok=True)


def supervise(binary, origin, runtime, stop, protocol='http2'):
    retry = 2
    while not stop.is_set():
        tunnel = None
        try:
            clear_entry(runtime)
            while not stop.is_set() and not origin_instance(origin):
                stop.wait(2)
            if stop.is_set():
                break
            tunnel = Tunnel(binary, origin, {'mode': 'quick', 'protocol': protocol}, runtime)
            advertised = None
            deadline = time.monotonic() + 120
            while not stop.is_set():
                if tunnel.process.poll() is not None:
                    raise RuntimeError('隧道进程退出')
                instance = origin_instance(origin)
                if advertised is not None and instance != advertised[1]:
                    clear_entry(runtime)
                    advertised = None
                    deadline = time.monotonic() + 120
                if tunnel.url and tunnel.connected.is_set() and instance:
                    identity = (tunnel.url, instance)
                    if identity != advertised and check_public(tunnel.url, instance, timeout=5):
                        publish_entry(runtime, *identity)
                        advertised = identity
                        retry = 2
                        print('公网入口已验证：' + tunnel.url, flush=True)
                if advertised is None and time.monotonic() >= deadline:
                    raise RuntimeError('公网入口验证超时')
                stop.wait(5 if advertised else 1)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f'隧道暂不可用：{exc}；{retry} 秒后重试，游戏服务继续运行。', flush=True)
        finally:
            clear_entry(runtime)
            if tunnel:
                tunnel.close()
        if not stop.is_set():
            stop.wait(retry)
            retry = min(60, retry * 2)


def main():
    parser = argparse.ArgumentParser(description='常驻免域名隧道；独立于游戏服务，失败自动重连')
    parser.add_argument('--origin', default='http://127.0.0.1:8765')
    parser.add_argument('--runtime', type=Path, default=RUNTIME)
    parser.add_argument('--cloudflared', type=Path)
    parser.add_argument('--protocol', choices=('http2', 'quic', 'auto'), default='http2')
    args = parser.parse_args()
    origin = validate_origin(args.origin)
    args.runtime.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    lease = LauncherLease(args.runtime)
    try:
        binary = args.cloudflared.resolve() if args.cloudflared else install_cloudflared()
        supervise(binary, origin, args.runtime, stop, args.protocol)
    finally:
        clear_entry(args.runtime)
        lease.close()


if __name__ == '__main__':
    main()
