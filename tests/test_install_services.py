import unittest
import tempfile
from pathlib import Path as LocalPath
from pathlib import PurePosixPath as Path
from unittest.mock import patch
from types import SimpleNamespace

from tools.install_services import units, main


class ServicePaths(unittest.TestCase):
    def test_live_game_unit_uses_gateway_and_unchanged_http_port(self):
        definitions = units(Path('/home/test/tractor'), Path('/usr/bin/python3'), live=True)
        self.assertIn('-m tractor_sim.live --host 127.0.0.1 --port 8765', definitions['tractor.service'])
        self.assertIn('--origin http://127.0.0.1:8765', definitions['tractor-tunnel.service'])

    def test_game_only_preserves_tunnel_definition_and_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = LocalPath(folder) / 'tractor'
            root.mkdir()
            config = LocalPath(folder) / 'config'
            tunnel = config / 'systemd' / 'user' / 'tractor-tunnel.service'
            tunnel.parent.mkdir(parents=True)
            tunnel.write_text('Existing tunnel')
            with patch('tools.install_services.ROOT', root), \
                 patch('tools.install_services.sys.argv', ['install_services', '--live', '--game-only']), \
                 patch('tools.install_services.sys.platform', 'linux'), \
                 patch.dict('os.environ', {'USER': 'test', 'XDG_CONFIG_HOME': str(config)}), \
                 patch('tools.install_services.shutil.which', return_value='/usr/bin/ssh'), \
                 patch('tools.install_services.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
                main()
            self.assertEqual(tunnel.read_text(), 'Existing tunnel')
            commands = [c.args[0] for c in run.call_args_list]
            self.assertNotIn(['systemctl', '--user', 'restart', 'tractor-tunnel.service'], commands)
            self.assertIn(['systemctl', '--user', 'restart', 'tractor.service'], commands)
    def test_switching_only_relay_never_restarts_or_rewrites_game_service(self):
        with tempfile.TemporaryDirectory() as folder:
            root = LocalPath(folder) / 'tractor'
            root.mkdir()
            config = LocalPath(folder) / 'config'
            game = config / 'systemd' / 'user' / 'tractor.service'
            game.parent.mkdir(parents=True)
            game.write_text('Existing game definition')
            with patch('tools.install_services.ROOT', root), \
                 patch('tools.install_services.sys.argv', ['install_services', '--provider', 'localhost-run', '--tunnel-only']), \
                 patch('tools.install_services.sys.platform', 'linux'), \
                 patch.dict('os.environ', {'USER':'test', 'XDG_CONFIG_HOME':str(config)}), \
                 patch('tools.install_services.shutil.which', return_value='/usr/bin/ssh'), \
                 patch('tools.install_services.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
                main()
            self.assertEqual(game.read_text(), 'Existing game definition')
            commands = [c.args[0] for c in run.call_args_list]
            self.assertNotIn(['systemctl', '--user', 'restart', 'tractor.service'], commands)
            self.assertIn(['systemctl', '--user', 'restart', 'tractor-tunnel.service'], commands)
            self.assertIn('--provider localhost-run', (game.parent / 'tractor-tunnel.service').read_text())

    def test_relay_provider_is_explicit_and_cannot_inject_unit_text(self):
        unit = units(Path('/home/test/tractor'), Path('/usr/bin/python3'), provider='localhost-run')['tractor-tunnel.service']
        self.assertIn('--provider localhost-run', unit)
        with self.assertRaises(ValueError):
            units(Path('/home/test/tractor'), Path('/usr/bin/python3'), provider='cloudflare\nExecStart=bad')

    def test_working_directory_is_an_absolute_path_not_a_quoted_command_argument(self):
        definitions = units(Path('/home/test user/tractor'), Path('/usr/bin/python3'))
        for unit in definitions.values():
            self.assertIn('\nWorkingDirectory=/home/test user/tractor\n', unit)
            self.assertNotIn('WorkingDirectory="', unit)
        self.assertIn('ExecStart="/usr/bin/python3"', definitions['tractor.service'])
        self.assertIn('--history-dir "/home/test user/tractor/runtime/history"', definitions['tractor.service'])

    def test_path_specifiers_are_escaped_and_newlines_are_rejected(self):
        definitions = units(Path('/home/percent%/tractor'), Path('/usr/bin/python3'))
        self.assertIn('WorkingDirectory=/home/percent%%/tractor\n', definitions['tractor.service'])
        with self.assertRaises(ValueError): units(Path('/home/new\nline'), Path('/usr/bin/python3'))
