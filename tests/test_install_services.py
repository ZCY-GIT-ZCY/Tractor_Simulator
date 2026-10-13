import unittest
from pathlib import PurePosixPath as Path

from tools.install_services import units


class ServicePaths(unittest.TestCase):
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
