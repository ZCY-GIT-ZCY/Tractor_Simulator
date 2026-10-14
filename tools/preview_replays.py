"""Serve an offline replay collection locally, without starting game rooms."""
import argparse
import json
import mimetypes
import sys
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tractor_sim.history import HistoryStore


class ReplayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, directory):
        self.history = HistoryStore(directory)
        self.directory = Path(directory).resolve()
        self.web = ROOT / 'web'
        super().__init__(address, ReplayHandler)


class ReplayHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, value, status=200):
        self.send_body(json.dumps(value, ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8', status)

    def send_body(self, body, mime, status=200):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path == '/api/health':
                self.reply({'ok': True, 'service': 'tractor_replay_preview',
                            'history_dir': str(self.server.directory),
                            'records': len(self.server.history.index)})
            elif path == '/api/history':
                self.reply(self.server.history.listing())
            elif path.startswith('/api/history/'):
                try:
                    self.reply(self.server.history.read(path.removeprefix('/api/history/')))
                except KeyError:
                    self.reply({'message': '这份回放不存在'}, 404)
            else:
                relative = path.lstrip('/') or 'history.html'
                target = (self.server.web / relative).resolve()
                if not target.is_relative_to(self.server.web.resolve()) or not target.is_file():
                    self.reply({'message': '文件不存在'}, 404)
                    return
                mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript',
                        '.svg': 'image/svg+xml'}.get(target.suffix,
                        mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                self.send_body(target.read_bytes(), mime + '; charset=utf-8')
        except (OSError, ValueError) as exc:
            print(f'Replay read failed: {exc}', file=sys.stderr, flush=True)
            self.reply({'message': '回放读取失败，请检查本地记录'}, 500)

    def do_POST(self):
        self.reply({'message': '此服务仅支持查看回放'}, 405)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='包含完整回放 JSON 的目录')
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error('回放目录不存在')
    address = f'http://127.0.0.1:{args.port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(address + '/api/health', timeout=1) as response:
            health = json.load(response)
        if (health.get('service') == 'tractor_replay_preview' and
                health.get('history_dir') == str(args.directory.resolve())):
            if not args.no_browser:
                webbrowser.open(address + '/history.html')
            print('Replay preview already running: ' + address, flush=True)
            return
    except (OSError, ValueError):
        pass
    try:
        server = ReplayServer(('127.0.0.1', args.port), args.directory)
    except OSError as exc:
        parser.exit(1, f'无法监听端口 {args.port}，请使用 --port 选择其他端口：{exc}\n')
    print(f'{address}/history.html — {len(server.history.index)} replays', flush=True)
    if not args.no_browser:
        webbrowser.open(address + '/history.html')
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
