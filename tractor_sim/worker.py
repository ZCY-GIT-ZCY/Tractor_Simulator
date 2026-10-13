"""Loopback-only game worker, owned by the stable gateway."""
import json
import os
from pathlib import Path

from .checkpoint import private_json
from .server import TractorServer


def main():
    settings = json.loads(Path(os.environ['TRACTOR_WORKER_CONFIG']).read_text(encoding='utf-8'))
    checkpoint = json.loads(Path(settings['checkpoint']).read_text(encoding='utf-8'))
    server = TractorServer(('127.0.0.1', 0), history_dir=settings['history'],
                           public_entry_file=settings['public_entry'],
                           instance_id=settings['instance_id'], checkpoint=checkpoint,
                           control_key=os.environ['TRACTOR_CONTROL_KEY'])
    private_json(settings['ready'], {'port': server.server_port, 'pid': os.getpid(),
                                    'instance_id': server.instance_id})
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
