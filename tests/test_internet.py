import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tractor_sim.internet import valid_public_url, check_public, LauncherLease, validate_config, Tunnel
from tractor_sim.server import Registry
from tractor_sim.rules import IllegalAction


class PublicRooms(unittest.TestCase):
    def test_generated_room_and_four_independent_sessions(self):
        registry = Registry()
        first = registry.enter({"mode": "pvp", "name": "host"})
        code = first["room"]
        self.assertRegex(code, r"^[A-Z2-9]{8}$")
        clients = [first] + [registry.enter({"mode": "pvp", "room": code, "name": str(i)}) for i in range(1, 4)]
        self.assertEqual([c["seat"] for c in clients], [0, 1, 2, 3])
        self.assertEqual(len({c["token"] for c in clients}), 4)
        for c in clients:
            room = registry.lookup(c["token"])
            with room.condition:
                state = room.snapshot(c["token"])["state"]
            self.assertNotIn("hands", state)
            self.assertIsNone(state["kitty"])

    def test_capacity_still_allows_joining_an_existing_room(self):
        registry = Registry(max_rooms=1)
        first = registry.enter({"mode": "pvp"})
        with self.assertRaises(IllegalAction) as error:
            registry.enter({"mode": "self"})
        self.assertEqual(error.exception.code, "ROOM_LIMIT")
        second = registry.enter({"mode": "pvp", "room": first["room"]})
        self.assertEqual(second["seat"], 1)

    def test_inactive_members_expire_together_but_active_room_keeps_seats(self):
        registry = Registry(idle_seconds=100)
        first = registry.enter({"mode": "pvp"})
        second = registry.enter({"mode": "pvp", "room": first["room"]})
        room = registry.lookup(first["token"])
        with room.condition:
            room.clients[first["token"]]["seen"] = time.monotonic() - 101
        registry.prune()
        self.assertIs(registry.lookup(first["token"]), room)
        with room.condition:
            room.clients[second["token"]]["seen"] = time.monotonic() - 101
        registry.prune()
        self.assertNotIn(first["room"], registry.rooms)
        self.assertFalse(registry.tokens)

    def test_public_url_validation(self):
        self.assertEqual(valid_public_url("https://tractor.example.com/"), "https://tractor.example.com")
        for value in ("http://example.com", "https://user:secret@example.com", "https://example.com/path",
                      "https://example.com?token=secret", "https://example.com#room", "https://"):
            with self.assertRaises(ValueError): valid_public_url(value)

    def test_readiness_must_reach_this_exact_server(self):
        import io
        response = {"service": "tractor_sim", "ok": True, "instance_id": "expected"}
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(json.dumps(response).encode())):
            self.assertTrue(check_public("https://example.com", "expected"))
            self.assertFalse(check_public("https://example.com", "different"))

    def test_launcher_lock_is_exclusive_and_reusable(self):
        with tempfile.TemporaryDirectory() as folder:
            first = LauncherLease(Path(folder))
            try:
                with self.assertRaises(OSError): LauncherLease(Path(folder))
            finally:
                first.close()
            second = LauncherLease(Path(folder)); second.close()

    def test_non_object_health_reply_is_not_readiness(self):
        import io
        for value in ([], None, 'tractor_sim', 1):
            with patch('urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(json.dumps(value).encode())):
                self.assertFalse(check_public('https://example.com'))

    def test_invalid_config_is_rejected_before_starting_services(self):
        self.assertEqual(validate_config({}), 8765)
        self.assertEqual(validate_config({'port': 0}), 0)
        self.assertEqual(validate_config({'port': 'wrong'}, 8766), 8766)
        for config in ([], None, {'port': -1}, {'port': 65536}, {'port': '8765'},
                       {'port': True}, {'max_rooms': 0}, {'max_rooms': '4'},
                       {'protocol': 'invalid'}, {'mode': 'unknown'}, {'mode': 'named', 'port': 0}):
            with self.subTest(config=config), self.assertRaises(ValueError): validate_config(config)

    def test_named_token_bom_normalized_and_removed_on_spawn_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            original = runtime / 'original-token.txt'
            original.write_text('example-test-token\n', encoding='utf-8-sig')
            observed = []
            def fail_spawn(command, **kwargs):
                credential = Path(command[command.index('--token-file') + 1])
                observed.append(credential)
                self.assertEqual(credential.read_bytes(), b'example-test-token')
                raise OSError('test executable missing')
            with patch('tractor_sim.internet.subprocess.Popen', side_effect=fail_spawn):
                with self.assertRaises(OSError):
                    Tunnel(Path('missing'), 'http://127.0.0.1:8765', {'mode': 'named',
                           'public_url': 'https://example.com', 'token_file': str(original)}, runtime)
            self.assertTrue(original.is_file()); self.assertFalse(observed[0].exists())
            # No open logfile survives the failed constructor (Windows enforces this).
            (runtime / 'tunnel.log').unlink()


if __name__ == "__main__": unittest.main()
