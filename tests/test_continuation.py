import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tractor_sim.engine import TractorEnv, GameConfig
from tractor_sim.history import HistoryStore, RoundRecording
from tractor_sim.server import Registry, TractorServer
from tractor_sim.checkpoint import load_registry
from test_rules import c, fixture
from test_mandatory_migration import recorded_results


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name) / 'history'
        self.registry = Registry(history_dir=self.directory)

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, *, banker=0, level=2, score=70, passed=None, levels=None):
        env = fixture([[53], c('S', 3), c('S', 4), c('S', 5)], level=level)
        env.banker = banker
        env.levels = levels[:] if levels else [level, level]
        env.passed = [set(p) for p in (passed or [[], []])]
        env.score = env.captured_score = score
        for _ in range(4):
            env.step({'type': 'play', 'cards': env.hands[env.current_player][:]})
        identifier = self.registry.history.save(RoundRecording(env), mode='pvp', room='old_table',
            players=[{'seat': s, 'name': '旧玩家' + str(s), 'bot': False} for s in range(4)],
            config={'initial_level': 2, 'auction': False, 'banker': banker})
        return identifier, env

    def test_restores_next_banker_levels_win_credit_and_fresh_deal(self):
        for banker, score in [(0, 70), (3, 150), (1, 70), (2, 150)]:
            with self.subTest(banker=banker, score=score):
                identifier, old = self.record(banker=banker, score=score)
                summary = self.registry.history.continuation(identifier)
                self.assertTrue(summary['available'])
                self.assertEqual(summary['next_banker'], old.result['next_banker'])
                first = self.registry.enter({'mode': 'pvp', 'name': '新玩家', 'continue_from': identifier,
                                             'config': {'auction': True, 'banker': 2, 'initial_level': 14}})
                room = self.registry.lookup(first['token'])
                self.assertFalse(first['started']); self.assertIsNone(first['state'])
                self.assertIsNone(first['seat']); self.assertEqual(room.clients[first['token']]['name'], '新玩家')
                self.assertEqual(room.env.levels, old.levels)
                self.assertEqual(room.env.passed, old.passed)
                self.assertEqual(room.env.banker, old.result['next_banker'])
                self.assertEqual(room.env.round_no, old.round_no + 1)
                self.assertEqual(room.env.level, old.levels[room.env.banker % 2])
                self.assertFalse(room.env.auction); self.assertIsNone(room.env.bid)
                self.assertIsNone(room.env.result); self.assertEqual(room.env.deal_count, 1)
                self.assertEqual(sum(map(len, room.env.hands)), 1)
                self.assertEqual(sorted(room.env.deck), list(range(108)))
                self.assertEqual(len(room.env.action_log), 0)
                self.assertEqual(first['continuation']['previous_players'][0]['name'], '旧玩家0')

    def test_can_choose_seats_fill_ai_and_start_same_authoritative_progress(self):
        identifier, old = self.record()
        host = self.registry.enter({'mode': 'pvp', 'name': '房主', 'continue_from': identifier})
        room = self.registry.lookup(host['token'])
        with room.condition:
            room.confirm_seat(host['token'], 3)
            room.set_ai(host['token'], None, True)
        guest = self.registry.enter({'mode': 'pvp', 'name': '朋友', 'room': room.code})
        with room.condition:
            room.confirm_seat(guest['token'], 1)
            room.start_room(host['token'])
            view = room.snapshot(host['token'])
        self.assertTrue(view['started']); self.assertEqual(view['ai_seats'], [0, 2])
        self.assertEqual(view['state']['banker'], old.banker)
        self.assertEqual(guest['continuation'], host['continuation'])
        self.assertNotIn('hands', view['state'])

    def test_win_credit_survives_later_special_downgrade_and_clamps_unwon_two(self):
        identifier, _ = self.record(score=150)
        p = self.registry.history.continuation(identifier)['progress']
        self.assertEqual(p['levels'], [2, 2]); self.assertEqual(p['passed_levels'], [[], []])
        restored = TractorEnv.from_progress(p, seed=1)
        self.assertEqual(restored._advance(1, 3), 2)
        p['levels'] = [2, 11]; p['passed_levels'] = [[2, 5, 10, 11], [2, 5, 10]]
        restored = TractorEnv.from_progress(p, seed=2)
        self.assertEqual(restored._advance(0, 7), 9)
        self.assertEqual(restored.passed[0], {2, 5, 10, 11})

    def test_two_forks_do_not_mutate_source_or_share_rng_or_progress(self):
        identifier, _ = self.record()
        source = copy.deepcopy(self.registry.history.read(identifier))
        rooms = [self.registry.lookup(self.registry.enter({'mode': 'pvp', 'name': '房主',
                  'continue_from': identifier})['token']) for _ in range(2)]
        self.assertNotEqual(rooms[0].code, rooms[1].code)
        self.assertNotEqual(rooms[0].env.deck, rooms[1].env.deck)
        rooms[0].env.passed[0].add(5)
        self.assertNotIn(5, rooms[1].env.passed[0])
        self.assertEqual(self.registry.history.read(identifier), source)

    def test_invalid_progress_reset_keeps_existing_game_unchanged(self):
        env = TractorEnv(seed=44)
        before = env.export_state()
        with self.assertRaises(ValueError):
            env.reset(seed=999, progress={'schema_version': 1})
        self.assertEqual(env.export_state(), before)

    def test_recent_existing_record_uses_final_frame_without_new_progress_field(self):
        identifier, old = self.record(level=5, levels=[5, 4], passed=[[2], [2, 3]])
        path = self.registry.history.index[identifier][0]
        document = json.loads(path.read_text(encoding='utf-8'))
        document['meta'].pop('progress')
        path.write_text(json.dumps(document), encoding='utf-8')
        summary = HistoryStore(self.directory).continuation(identifier)
        self.assertTrue(summary['available'])
        self.assertEqual(summary['levels'], old.levels)
        self.assertEqual(summary['passed_levels'], [sorted(p) for p in old.passed])
        self.assertEqual(summary['next_banker'], old.banker)

    def test_ended_match_invalid_id_and_existing_room_cannot_be_reopened(self):
        identifier, _ = self.record(level=14, score=70, levels=[14, 14])
        self.assertFalse(self.registry.history.continuation(identifier)['available'])
        for mode, source in [('pvp', identifier), ('self', identifier), ('pvp', '../x'), ('pvp', None)]:
            with self.subTest(mode=mode, source=source), self.assertRaises(ValueError):
                self.registry.enter({'mode': mode, 'name': '房主', 'continue_from': source})
        self.assertFalse(self.registry.rooms)
        identifier, _ = self.record()
        host = self.registry.enter({'mode': 'pvp', 'name': '原房主', 'room': 'occupied_room'})
        room = self.registry.lookup(host['token']); before = room.env.export_state()
        with self.assertRaises(ValueError):
            self.registry.enter({'mode': 'pvp', 'name': '新人', 'room': room.code, 'continue_from': identifier})
        self.assertEqual(room.env.export_state(), before); self.assertEqual(len(room.clients), 1)

    def test_new_record_after_continuation_preserves_lineage_and_cumulative_credit(self):
        identifier, _ = self.record()
        first = self.registry.enter({'mode': 'pvp', 'name': '房主', 'continue_from': identifier})
        room = self.registry.lookup(first['token'])
        # A deterministic last trick exercises normal server recording/save.
        end = fixture([[53], c('S', 3), c('S', 4), c('S', 5)], level=3)
        end.round_no = 2; end.levels = [3, 2]; end.passed = [{2}, set()]
        room.env = end; room.recording = RoundRecording(end)
        with room.condition:
            for _ in range(4):
                room.step({'type': 'play', 'cards': room.env.hands[room.env.current_player][:]})
        meta = self.registry.history.read(room.replay_id)['meta']
        self.assertEqual(meta['continued_from'], identifier)
        self.assertEqual(meta['progress']['passed_levels'][0], [2, 3])
        fresh = HistoryStore(self.directory).continuation(room.replay_id)
        self.assertTrue(fresh['available']); self.assertEqual(fresh['next_round'], 3)

    def test_rolling_checkpoint_keeps_continuation_and_older_rooms_default_to_none(self):
        identifier, _ = self.record()
        first = self.registry.enter({'mode': 'pvp', 'name': '房主', 'continue_from': identifier})
        document = json.loads(json.dumps(self.registry.freeze()))
        target = Registry(history_dir=self.directory)
        load_registry(target, document)
        room = target.lookup(first['token'])
        self.assertEqual(room.continuation, first['continuation'])
        self.assertEqual(room.env.passed, [{2}, set()])
        document['rooms'][0].pop('continuation')
        load_registry(target, document)
        self.assertIsNone(target.lookup(first['token']).continuation)

    def legacy_records(self):
        self.directory.mkdir(exist_ok=True)
        start = datetime(2026, 10, 15, tzinfo=timezone.utc)
        ids = []
        for event in recorded_results():
            n = event['round']; identifier = f'{n:032x}'; ids.append(identifier)
            result = {k: v for k, v in event.items() if k not in ('type', 'seq')}
            result['next_banker'] = (result['banker'] - (2 if result['score'] < 80 else 1)) % 4
            meta = {'id': identifier, 'filename': str(n) + '.json', 'room': 'legacy', 'mode': 'pvp',
                'config': {'initial_level': 2, 'auction': False, 'banker': 0}, 'round': n,
                'level': result['level'], 'players': [], 'result': result,
                'started_at': (start + timedelta(minutes=n*10)).isoformat(),
                'finished_at': (start + timedelta(minutes=n*10+9)).isoformat()}
            state = {'phase': 'round_end', 'result': result, 'kitty_public': False, 'kitty': None}
            doc = {'schema_version': 1, 'meta': meta,
                   'frames': [{'state': state, 'action': None}]}
            (self.directory / meta['filename']).write_text(json.dumps(doc), encoding='utf-8')
        return HistoryStore(self.directory), ids

    def test_legacy_reported_eight_hands_rebuild_only_defending_wins(self):
        store, ids = self.legacy_records()
        summary = store.continuation(ids[-1])
        self.assertTrue(summary['available']); self.assertTrue(summary['mandatory_corrected'])
        self.assertEqual(summary['levels'], [8, 2])
        self.assertEqual(summary['passed_levels'], [[2, 3, 4, 5, 7], []])
        self.assertEqual(summary['next_banker'], 3)
        self.assertEqual(summary['next_level'], 2)
        self.assertEqual(store.read(ids[-1])['meta']['result']['levels_after'], [8, 3])

    def test_legacy_incomplete_history_never_invents_mandatory_credit(self):
        store, ids = self.legacy_records()
        (self.directory / '3.json').unlink()
        summary = HistoryStore(self.directory).continuation(ids[-1])
        self.assertFalse(summary['available']); self.assertIn('第 3 局缺失', summary['message'])

    def test_legacy_chain_compares_absolute_times_across_timezones(self):
        store, ids = self.legacy_records()
        for path in self.directory.glob('*.json'):
            document = json.loads(path.read_text(encoding='utf-8'))
            if document['meta']['round'] % 2:
                for key in ('started_at', 'finished_at'):
                    instant = datetime.fromisoformat(document['meta'][key])
                    document['meta'][key] = instant.astimezone(timezone(timedelta(hours=8))).isoformat()
                path.write_text(json.dumps(document), encoding='utf-8')
        summary = HistoryStore(self.directory).continuation(ids[-1])
        self.assertTrue(summary['available']); self.assertEqual(summary['levels'], [8, 2])

    def test_http_preview_and_admission_use_server_record_not_client_state(self):
        identifier, _ = self.record()
        server = TractorServer(('127.0.0.1', 0), history_dir=self.directory)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def req(path, body=None):
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}' + path,
                headers={'Content-Type': 'application/json'},
                data=None if body is None else json.dumps(body).encode())
            try:
                with opener.open(request, timeout=5) as response: return response.status, json.load(response)
            except urllib.error.HTTPError as error: return error.code, json.load(error)
        try:
            status, preview = req('/api/history/' + identifier + '/continuation')
            self.assertEqual(status, 200); self.assertEqual(preview['next_round'], 2)
            self.assertNotIn('hands', json.dumps(preview)); self.assertNotIn('kitty', json.dumps(preview))
            status, room = req('/api/session', {'mode': 'pvp', 'name': '房主', 'continue_from': identifier,
                'config': {'initial_level': 14}, 'passed_levels': [[14], [14]], 'next_banker': 1})
            self.assertEqual(status, 200); self.assertEqual(room['config']['initial_level_a'], 3)
            self.assertEqual(room['continuation']['next_banker'], 2)
            self.assertEqual(req('/api/history/' + 'f'*32 + '/continuation')[0], 404)
        finally:
            server.shutdown(); server.server_close(); worker.join(2)


if __name__ == '__main__':
    unittest.main()
