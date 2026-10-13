"""Stage and submit an update to an already-running live gateway. Never restart."""
import argparse
import json
import sys
import time
import uuid
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tractor_sim.checkpoint import private_json
from tractor_sim.live import stage_release


def submit(source, operations, port=8765, timeout=60, reset_rooms=False):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=5) as response:
        health = json.load(response)
    if health.get('live_update') != 1:
        raise RuntimeError('当前是旧版直接服务，没有滚动更新入口；未停止或重启任何进程。首次需启用网关。')
    operations = Path(operations).resolve()
    state = json.loads((operations / 'gateway.json').read_text(encoding='utf-8'))
    if state['instance_id'] != health['instance_id'] or state['port'] != port:
        raise ValueError('运行目录与监听中的网关不匹配')
    release = stage_release(source, operations)
    tag = uuid.uuid4().hex
    request = operations / 'requests' / (tag + '.request.json')
    result = operations / 'requests' / (tag + '.result.json')
    private_json(request, {'instance_id': health['instance_id'], 'release': release.name, 'reset_rooms': reset_rooms})
    deadline = time.monotonic() + timeout
    while not result.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('更新尚未返回结果；查看 runtime/live/requests，勿直接重启服务')
        time.sleep(0.1)
    outcome = json.loads(result.read_text(encoding='utf-8'))
    if not outcome.get('ok'):
        raise RuntimeError('更新失败，已保留旧版：' + outcome.get('message', '未知错误'))
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--operations', type=Path, default=ROOT / 'runtime' / 'live')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--reset-rooms', action='store_true', help='结束全部牌局和计算，保留网关与公网域名')
    args = parser.parse_args()
    try:
        print(json.dumps(submit(args.source, args.operations, args.port, reset_rooms=args.reset_rooms), ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
