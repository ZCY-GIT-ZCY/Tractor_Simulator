"""Install the game's two independent Linux user services, without sudo."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def quoted(value):
    return '"' + str(value).replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"') + '"'


def units(root, python, port=8765, host='127.0.0.1', provider='cloudflare'):
    if provider not in ('cloudflare', 'localhost-run'):
        raise ValueError('未知公网中继')
    # WorkingDirectory is a single path, not an ExecStart word list. Quoting
    # it makes systemd treat the quote as the first character of a relative path.
    directory = str(root).replace('%', '%%')
    if '\n' in directory or '\r' in directory:
        raise ValueError('项目目录不能包含换行符')
    common = (f'WorkingDirectory={directory}\nEnvironment=PYTHONUTF8=1\n'
              'Environment=PYTHONDONTWRITEBYTECODE=1\nRestart=on-failure\nRestartSec=5\n'
              'TimeoutStopSec=30\nNoNewPrivileges=true\nUMask=0077\n'
              'StandardOutput=journal\nStandardError=journal\n')
    game = ('[Unit]\nDescription=Tractor Simulator game server\nAfter=network.target\n\n'
            '[Service]\nType=simple\n' + common +
            f'ExecStart={quoted(python)} -u -m tractor_sim --serve --headless --host {host} '
            f'--port {port} --history-dir {quoted(root / "runtime" / "history")}\n\n'
            '[Install]\nWantedBy=default.target\n')
    tunnel = ('[Unit]\nDescription=Tractor outbound internet relay\n'
              'After=network.target tractor.service\nWants=tractor.service\n\n'
              '[Service]\nType=simple\n' + common +
              f'ExecStart={quoted(python)} -u -m tractor_sim.tunnel_service '
              f'--origin http://127.0.0.1:{port} --provider {provider}\n\n[Install]\nWantedBy=default.target\n')
    return {'tractor.service': game, 'tractor-tunnel.service': tunnel}


def main():
    parser = argparse.ArgumentParser(description='安装双升 Linux 用户服务；会重启本项目游戏服务')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', choices=('127.0.0.1', '0.0.0.0'), default='127.0.0.1')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--provider', choices=('cloudflare', 'localhost-run'), default='cloudflare')
    parser.add_argument('--tunnel-only', action='store_true', help='只切换/重启公网中继，保留当前游戏进程')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port 须为 1..65535')
    definitions = units(ROOT, Path(sys.executable), args.port, args.host, args.provider)
    if args.dry_run:
        for name, body in definitions.items():
            print(name + '\n' + body)
        return
    if not sys.platform.startswith('linux'):
        parser.error('此工具用于 Linux systemd 用户服务；Windows 使用 run-public.bat')
    # Finish preparation before replacing/restarting any existing service.
    if args.provider == 'cloudflare':
        from tractor_sim.internet import install_cloudflared
        install_cloudflared()
    elif not shutil.which('ssh'):
        parser.error('localhost-run 需要系统 OpenSSH 客户端 ssh')
    if args.tunnel_only and subprocess.run(
            ['systemctl', '--user', 'cat', 'tractor.service'], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL).returncode != 0:
        parser.error('--tunnel-only 需要已有 tractor.service 游戏服务')
    runtime = ROOT / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='unit-check-', dir=runtime) as folder:
        candidates = []
        for name, body in definitions.items():
            candidate = Path(folder) / name
            candidate.write_text(body, encoding='utf-8')
            candidates.append(str(candidate))
        subprocess.run(['systemd-analyze', '--user', 'verify', *candidates], check=True)
    directory = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd' / 'user'
    directory.mkdir(parents=True, exist_ok=True)
    if args.tunnel_only:
        definitions = {'tractor-tunnel.service': definitions['tractor-tunnel.service']}
    for name, body in definitions.items():
        path = directory / name
        temporary = path.with_suffix('.tmp')
        temporary.write_text(body, encoding='utf-8')
        temporary.replace(path)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', *definitions], check=True)
    if not args.tunnel_only:
        subprocess.run(['systemctl', '--user', 'restart', 'tractor.service'], check=True)
    subprocess.run(['systemctl', '--user', 'restart', 'tractor-tunnel.service'], check=True)
    linger = subprocess.run(['loginctl', '--no-ask-password', 'enable-linger', os.environ['USER']])
    if linger.returncode:
        print('请管理员为本用户启用 loginctl enable-linger，以便退出 SSH 后和开机时常驻。')
    print('服务已启动。公网验证成功后，入口写入 runtime/public-url.txt。')


if __name__ == '__main__':
    # Support both python -m tools.install_services and direct script execution.
    sys.path.insert(0, str(ROOT))
    main()
