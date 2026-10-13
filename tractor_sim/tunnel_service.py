"""Persistent outbound tunnel for an independently managed game server.

Restarting this process never restarts the game or discards rooms. A new entry
is published only after public HTTPS health reaches the current game instance.
"""
import argparse
import json
import signal
import shutil
import threading
import time
import urllib.request
from pathlib import Path

from .internet import (RUNTIME, LauncherLease, Tunnel, check_public, install_cloudflared,
                       valid_public_url, validate_local_origin as validate_origin)


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


def create_tunnel(provider, binary, origin, runtime, protocol):
    if provider == 'cloudflare':
        return Tunnel(binary, origin, {'mode': 'quick', 'protocol': protocol}, runtime)
    if provider == 'localhost-run':
        from .ssh_relay import SSHRelay
        return SSHRelay(binary, origin, runtime)
    raise ValueError('未知公网中继：' + provider)


def supervise(binary, origin, runtime, stop, protocol='http2', provider='cloudflare'):
    retry = 2
    while not stop.is_set():
        tunnel = None
        try:
            clear_entry(runtime)
            while not stop.is_set() and not origin_instance(origin):
                stop.wait(2)
            if stop.is_set():
                break
            tunnel = create_tunnel(provider, binary, origin, runtime, protocol)
            verify = getattr(tunnel, 'verify_public', check_public)
            advertised = None
            last_public_check = 0
            public_failures = 0
            deadline = time.monotonic() + 120
            while not stop.is_set():
                if tunnel.process.poll() is not None:
                    raise RuntimeError('隧道进程退出')
                instance = origin_instance(origin)
                if advertised is not None and (instance != advertised[1] or tunnel.url != advertised[0]):
                    clear_entry(runtime)
                    advertised = None
                    deadline = time.monotonic() + 120
                if tunnel.url and tunnel.connected.is_set() and instance:
                    identity = (tunnel.url, instance)
                    if identity != advertised and verify(tunnel.url, instance, timeout=5):
                        publish_entry(runtime, *identity)
                        advertised = identity
                        last_public_check = time.monotonic()
                        public_failures = 0
                        retry = 2
                        print('公网入口已验证：' + tunnel.url, flush=True)
                if advertised is not None and time.monotonic() - last_public_check >= 30:
                    last_public_check = time.monotonic()
                    public_failures = 0 if verify(tunnel.url, instance, timeout=5) else public_failures + 1
                    if public_failures >= 2:
                        clear_entry(runtime)
                        advertised = None
                        deadline = time.monotonic() + 120
                if (advertised is None and time.monotonic() >= deadline and
                        not getattr(tunnel, 'preserve_address', False)):
                    raise RuntimeError('公网入口验证超时')
                stop.wait(5 if advertised else getattr(tunnel, 'unverified_poll_seconds', 1))
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
    parser.add_argument('--provider', choices=('cloudflare', 'localhost-run'), default='cloudflare')
    parser.add_argument('--protocol', choices=('http2', 'quic', 'auto'), default='http2')
    args = parser.parse_args()
    origin = validate_origin(args.origin)
    args.runtime.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    lease = LauncherLease(args.runtime)
    try:
        if args.provider == 'cloudflare':
            binary = args.cloudflared.resolve() if args.cloudflared else install_cloudflared()
        else:
            if args.cloudflared:
                parser.error('--cloudflared 仅用于 cloudflare 中继')
            ssh = shutil.which('ssh')
            if not ssh:
                parser.error('SSH 中继需要系统 OpenSSH 客户端 ssh')
            binary = Path(ssh)
        supervise(binary, origin, args.runtime, stop, args.protocol, args.provider)
    finally:
        clear_entry(args.runtime)
        lease.close()


if __name__ == '__main__':
    main()
