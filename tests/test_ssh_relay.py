import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tractor_sim.ssh_relay import SSHRelay
from tractor_sim.tunnel_service import create_tunnel


class AnonymousSSHRelay(unittest.TestCase):
    def test_assignment_event_excludes_admin_docs_and_unrelated_domains(self):
        relay = SSHRelay.__new__(SSHRelay)
        relay.url = None
        relay.connected = threading.Event()
        for line in ('Welcome https://admin.localhost.run/',
                     'See https://fake.lhr.life/docs',
                     'fake tunneled with tls termination, https://fake.example.com',
                     'fake tunneled with tls termination, https://fake.lhr.life.evil.com'):
            relay.observe_output(line)
        self.assertIsNone(relay.url)
        self.assertFalse(relay.connected.is_set())
        relay.observe_output('\x1b[32m003abc.lhr.life tunneled with tls termination, https://003abc.lhr.life\x1b[0m\n')
        self.assertEqual(relay.url, 'https://003abc.lhr.life')
        self.assertTrue(relay.connected.is_set())
        relay.observe_output('new-name.lhr.life tunneled with tls termination, https://new-name.lhr.life')
        self.assertEqual(relay.url, 'https://new-name.lhr.life')

    def test_ssh_exposes_only_game_http_and_uses_no_existing_identity(self):
        process = SimpleNamespace(stdout=io.StringIO(''), poll=lambda: 0)
        with tempfile.TemporaryDirectory() as folder, \
             patch('tractor_sim.ssh_relay.subprocess.Popen', return_value=process) as start, \
             patch('tractor_sim.ssh_relay.kill_child_on_windows_exit', return_value=None):
            runtime = Path(folder)
            relay = SSHRelay(Path('ssh'), 'http://127.0.0.1:8765', runtime)
            try:
                command = start.call_args.args[0]
                self.assertIn('80:127.0.0.1:8765', command)
                self.assertIn('nokey@localhost.run', command)
                self.assertIn('IdentityAgent=none', command)
                self.assertIn('IdentityFile=none', command)
                self.assertIn('PreferredAuthentications=none', command)
                self.assertIn('StrictHostKeyChecking=accept-new', command)
                self.assertIn('UserKnownHostsFile=' + str(runtime / 'relay-known-hosts'), command)
                self.assertNotIn('StrictHostKeyChecking=no', command)
            finally:
                relay.close()

    def test_non_loopback_origin_is_rejected_before_starting_ssh(self):
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.Popen') as start:
            with self.assertRaises(ValueError):
                SSHRelay(Path('ssh'), 'http://example.com:8765', Path(folder))
            start.assert_not_called()

    def test_provider_selection_preserves_cloudflare_and_supports_ssh(self):
        with patch('tractor_sim.tunnel_service.Tunnel', return_value='cf') as cloudflare, \
             patch('tractor_sim.ssh_relay.SSHRelay', return_value='ssh') as ssh:
            self.assertEqual(create_tunnel('cloudflare', Path('cf'), 'http://localhost:8765', Path('.'), 'http2'), 'cf')
            self.assertEqual(create_tunnel('localhost-run', Path('ssh'), 'http://localhost:8765', Path('.'), 'http2'), 'ssh')
            cloudflare.assert_called_once()
            ssh.assert_called_once()
            with self.assertRaises(ValueError):
                create_tunnel('unknown', Path('ssh'), 'http://localhost:8765', Path('.'), 'http2')
