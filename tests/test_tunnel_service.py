import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tractor_sim.server import TractorServer
from tractor_sim.tunnel_service import origin_instance, validate_origin, publish_entry, supervise


class PersistentTunnel(unittest.TestCase):
    def test_origin_validation_and_exact_health_identity(self):
        self.assertEqual(validate_origin('http://127.0.0.1:8765/'), 'http://127.0.0.1:8765')
        for origin in ('https://localhost:8765', 'http://example.com:8765', 'http://localhost',
                       'http://user:secret@127.0.0.1:8765', 'http://localhost:8765/api', 'http://localhost:0'):
            with self.assertRaises(ValueError): validate_origin(origin)
        opener = SimpleNamespace(open=lambda *a, **k: io.BytesIO(json.dumps(
            {'ok': True, 'service': 'tractor_sim', 'instance_id': 'current'}).encode()))
        with patch('urllib.request.build_opener', return_value=opener):
            self.assertEqual(origin_instance('http://127.0.0.1:8765'), 'current')

    def test_sidecar_entry_cannot_advertise_an_old_game_or_malformed_url(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            server = TractorServer(('127.0.0.1', 0), history_dir=runtime / 'history',
                                   public_entry_file=runtime / 'public-entry.json')
            try:
                publish_entry(runtime, 'https://table.trycloudflare.com', 'old')
                self.assertIsNone(server.site_public_url())
                publish_entry(runtime, 'https://table.trycloudflare.com', server.instance_id)
                self.assertEqual(server.site_public_url(), 'https://table.trycloudflare.com')
                (runtime / 'public-entry.json').write_text(json.dumps(
                    {'instance_id': server.instance_id, 'public_url': 'javascript:alert(1)'}))
                self.assertIsNone(server.site_public_url())
                (runtime / 'public-entry.json').write_text('invalid')
                self.assertIsNone(server.site_public_url())
            finally:
                server.server_close()

    def test_tunnel_failure_retries_and_verifies_new_entry_without_a_game_restart(self):
        calls, closed, published = [], [], []
        class Stop:
            waits = 0
            def is_set(self): return self.waits >= 6
            def wait(self, delay): self.waits += 1
        class Connection:
            def is_set(self): return True
        class FakeTunnel:
            def __init__(self, *args):
                self.number = len(calls); calls.append(self); self.polls = 0
                self.url = f'https://table-{self.number}.trycloudflare.com'
                self.connected = Connection(); self.process = self
            def poll(self):
                self.polls += 1
                return 1 if self.number == 0 and self.polls > 1 else None
            def close(self): closed.append(self.number)
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            def publish(directory, url, instance):
                published.append((url, instance)); publish_entry(directory, url, instance)
            with patch('tractor_sim.tunnel_service.Tunnel', FakeTunnel), \
                 patch('tractor_sim.tunnel_service.origin_instance', return_value='same-game'), \
                 patch('tractor_sim.tunnel_service.check_public', return_value=True), \
                 patch('tractor_sim.tunnel_service.publish_entry', side_effect=publish):
                supervise(Path('fake'), 'http://127.0.0.1:8765', runtime, Stop())
            self.assertEqual(published, [('https://table-0.trycloudflare.com', 'same-game'),
                                         ('https://table-1.trycloudflare.com', 'same-game')])
            self.assertEqual(closed, [0, 1])
            self.assertFalse((runtime / 'public-entry.json').exists())
            self.assertFalse((runtime / 'public-url.txt').exists())
