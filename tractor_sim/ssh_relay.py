"""Anonymous localhost.run HTTP relay using the system OpenSSH client."""
import re
import os
import subprocess
import threading
from urllib.parse import urlparse

from .internet import Tunnel, kill_child_on_windows_exit, validate_local_origin

RELAY_URL = re.compile(r'https://[a-z0-9-]+\.lhr\.life(?=$|[\s,])')
ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


class SSHRelay(Tunnel):
    def __init__(self, binary, origin, runtime):
        parsed = urlparse(validate_local_origin(origin))
        host = f'[{parsed.hostname}]' if ':' in parsed.hostname else parsed.hostname
        self.runtime = runtime
        self.process = self.reader = self.job = self.log = self.token_copy = None
        self.url = None
        self.connected = threading.Event()
        runtime.mkdir(parents=True, exist_ok=True)
        command = [str(binary), '-T', '-p', '22',
                   '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                   '-o', 'IdentityAgent=none', '-o', 'IdentitiesOnly=yes',
                   '-o', 'IdentityFile=none', '-o', 'PreferredAuthentications=none',
                   '-o', 'UserKnownHostsFile=' + str(runtime / 'relay-known-hosts'),
                   '-o', 'StrictHostKeyChecking=accept-new',
                   '-o', 'ExitOnForwardFailure=yes',
                   '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
                   '-R', f'80:{host}:{parsed.port}', 'nokey@localhost.run']
        try:
            self.log = (runtime / 'tunnel.log').open('a', encoding='utf-8')
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace', cwd=str(runtime),
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.job = kill_child_on_windows_exit(self.process)
            self.reader = threading.Thread(target=self.read_output, daemon=True, name='tractor-ssh-relay-log')
            self.reader.start()
        except BaseException:
            self.close()
            raise

    def observe_output(self, line):
        line = ANSI_ESCAPE.sub('', line)
        # The provider prints links to its admin/docs pages before assigning a
        # tunnel. Only the assignment event is an origin URL, and health must
        # still verify it before the supervisor publishes it.
        if 'tunneled with tls termination' in line:
            found = RELAY_URL.search(line)
            if found:
                self.url = found.group()
                self.connected.set()
