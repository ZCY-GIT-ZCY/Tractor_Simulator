"""A source bundle must run without files from the developer's parent folder."""
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools import package_web


class SourceDeployment(unittest.TestCase):
    def test_clean_bundle_serves_web_rules_and_api(self):
        build = package_web.ROOT / 'build'
        build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build) as folder:
            directory = Path(folder)
            output = directory / 'deployment.zip'
            with patch.object(package_web, 'OUTPUT', output), contextlib.redirect_stdout(io.StringIO()):
                package_web.main()
            with zipfile.ZipFile(output) as archive:
                manifest = json.loads(archive.read('TractorWeb/manifest.json'))
                for relative, digest in manifest.items():
                    self.assertEqual(hashlib.sha256(archive.read('TractorWeb/' + relative)).hexdigest(), digest)
                self.assertIn('rules/双升游戏规则.md', manifest)
                self.assertFalse(any(name.startswith('runtime/') or name.endswith(('.log', '.pid'))
                                     for name in manifest))
                archive.extractall(directory)
            script = '''
import json, threading, urllib.request
from pathlib import Path
from tractor_sim.server import TractorServer
server = TractorServer(('127.0.0.1', 0))
thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
try:
    url = f'http://127.0.0.1:{server.server_port}'
    for name in ('/', '/app.js', '/style.css', '/rules'):
        with urllib.request.urlopen(url + name, timeout=5) as reply:
            content = reply.read()
            assert reply.status == 200 and content
            if name == '/rules': assert content == next(Path('rules').glob('*.md')).read_bytes()
    with urllib.request.urlopen(url + '/api/health', timeout=5) as reply:
        assert json.load(reply)['service'] == 'tractor_sim'
finally:
    server.shutdown(); server.server_close(); thread.join(2)
print('isolated source deployment OK')
'''
            completed = subprocess.run([sys.executable, '-c', script], cwd=directory / 'TractorWeb',
                                       capture_output=True, text=True, timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('isolated source deployment OK', completed.stdout)


if __name__ == '__main__': unittest.main()
