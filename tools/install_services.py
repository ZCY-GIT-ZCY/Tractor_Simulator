"""Install the game's two independent Linux user services, without sudo."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def quoted(value):
    return '"' + str(value).replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"') + '"'


def units(root, python, port=8765, host='127.0.0.1'):
    common = (f'WorkingDirectory={quoted(root)}\nEnvironment=PYTHONUTF8=1\n'
              'Environment=PYTHONDONTWRITEBYTECODE=1\nRestart=on-failure\nRestartSec=5\n'
              'TimeoutStopSec=30\nNoNewPrivileges=true\nUMask=0077\n'
              'StandardOutput=journal\nStandardError=journal\n')
    game = ('[Unit]\nDescription=Tractor Simulator game server\nAfter=network.target\n\n'
            '[Service]\nType=simple\n' + common +
            f'ExecStart={quoted(python)} -u -m tractor_sim --serve --headless --host {host} '
            f'--port {port} --history-dir {quoted(root / "runtime" / "history")}\n\n'
            '[Install]\nWantedBy=default.target\n')
    tunnel = ('[Unit]\nDescription=Tractor outbound Cloudflare tunnel\n'
              'After=network.target tractor.service\nWants=tractor.service\n\n'
              '[Service]\nType=simple\n' + common +
              f'ExecStart={quoted(python)} -u -m tractor_sim.tunnel_service '
              f'--origin http://127.0.0.1:{port}\n\n[Install]\nWantedBy=default.target\n')
    return {'tractor.service': game, 'tractor-tunnel.service': tunnel}


def main():
    parser = argparse.ArgumentParser(description='安装双升 Linux 用户服务；会重启本项目游戏服务')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', choices=('127.0.0.1', '0.0.0.0'), default='127.0.0.1')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port 须为 1..65535')
    definitions = units(ROOT, Path(sys.executable), args.port, args.host)
    if args.dry_run:
        for name, body in definitions.items():
            print(name + '\n' + body)
        return
    if not sys.platform.startswith('linux'):
        parser.error('此工具用于 Linux systemd 用户服务；Windows 使用 run-public.bat')
    # Finish preparation before replacing/restarting any existing service.
    from tractor_sim.internet import install_cloudflared
    install_cloudflared()
    directory = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd' / 'user'
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in definitions.items():
        path = directory / name
        temporary = path.with_suffix('.tmp')
        temporary.write_text(body, encoding='utf-8')
        temporary.replace(path)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', *definitions], check=True)
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
