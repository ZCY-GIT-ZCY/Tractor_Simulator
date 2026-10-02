import contextlib
import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from tractor_sim.__main__ import main
from tractor_sim.server import TractorServer


class Launcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = TractorServer(('127.0.0.1', 0))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(2)

    def invoke(self, port, extra=(), host='127.0.0.1'):
        output = io.StringIO()
        with patch('sys.argv', ['tractor_sim', '--host', host, '--port', str(port), *extra]), \
             patch('tractor_sim.__main__.webbrowser.open') as browser, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                main(); exit_code = 0
            except SystemExit as exc:
                exit_code = exc.code
            calls = browser.call_args_list
        return exit_code, output.getvalue(), calls

    def test_relaunch_opens_existing_table_without_replacing_server(self):
        code, output, calls = self.invoke(self.server.server_port)
        self.assertEqual(code, 0)
        self.assertIn('继续使用已有服务', output)
        self.assertEqual(calls[0].args, (self.url,)); self.assertEqual(len(calls), 1)
        self.assertFalse(self.server.registry.stopped.is_set())

    def test_headless_relaunch_does_not_open_browser(self):
        code, output, calls = self.invoke(self.server.server_port, ['--serve', '--headless'])
        self.assertEqual(code, 0); self.assertIn('已在运行', output); self.assertEqual(calls, [])

    def test_different_binding_is_not_silently_reused(self):
        code, output, calls = self.invoke(self.server.server_port, host='0.0.0.0')
        self.assertEqual(code, 1); self.assertIn('相同监听地址', output); self.assertEqual(calls, [])

    def test_other_http_service_gets_clear_error_without_browser(self):
        class OtherService(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({'ok': True, 'schema_version': 1, 'service': 'another_app'}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def log_message(self, *args): pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), OtherService)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            code, output, calls = self.invoke(server.server_port)
            self.assertEqual(code, 1); self.assertIn('端口', output)
            self.assertIn('--port', output); self.assertNotIn('Traceback', output)
            self.assertEqual(calls, [])
        finally:
            server.shutdown(); server.server_close(); thread.join(2)


if __name__ == '__main__':
    unittest.main()
