import copy
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from tractor_sim.engine import TractorEnv, GameConfig
from tractor_sim.history import HistoryStore, RoundRecording
from tractor_sim.policy import RandomPolicy
from tractor_sim.rules import IllegalAction
from tractor_sim.cards import effective_suit
from tractor_sim.server import Registry, TractorServer

ROOT = Path(__file__).resolve().parent.parent


class HistoryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.directory = Path(self.tmp.name) / "history"

    def tearDown(self):
        self.tmp.cleanup()

    def registry(self, **kwargs):
        return Registry(policy_factory=RandomPolicy, history_dir=self.directory, **kwargs)

    def skip_bidding_wait(self, room):
        # Replay tests use a virtual clock for the server's real-time pause;
        # clock and request races are exercised in test_realtime_bidding.
        if room.bidding_deadline is None:return False
        with room.condition:return room.close_bidding_if_due(room.bidding_deadline)

    def finish(self, room, seed=7):
        policy = RandomPolicy(seed)
        for _ in range(1500):
            if room.env.phase in ("round_end", "match_end"):
                return
            if self.skip_bidding_wait(room):continue
            room.step(policy.act(room.env.observe(room.env.current_player)))
        self.fail("round did not finish")

    def test_save_restart_exact_snapshots_and_hidden_bottom_projection(self):
        registry = self.registry()
        session = registry.enter({"mode": "self", "seed": 11,
                                  "config": {"initial_level": 11, "auction": False}})
        room = registry.lookup(session["token"])
        with room.condition:
            self.finish(room)
            ending = copy.deepcopy(room.recording.frames[-1]["state"])
            identifier = room.replay_id
        meta = registry.history.listing()["records"][0]
        self.assertTrue(meta["filename"].startswith("打J_"))
        self.assertEqual(meta["steps"], len(room.recording.frames) - 1)
        self.assertEqual(meta["result"], room.env.result)
        loaded = HistoryStore(self.directory)
        self.assertEqual(loaded.listing(), registry.history.listing())
        replay = loaded.read(identifier)
        self.assertEqual(replay["frames"][-1]["state"], ending)
        self.assertIsNone(ending["kitty"])
        self.assertEqual(ending['settlement_kitty'], room.env.kitty)
        self.assertGreater(sum(bool(f["action"] and f["action"]["type"] == "bury") for f in replay["frames"]), 0)
        for f in replay["frames"]:
            if f['state']['phase'] not in ('round_end', 'match_end'):
                self.assertNotIn('settlement_kitty', f['state'])
            self.assertNotIn("deck", f["state"])
            self.assertNotIn("rng_state", f["state"])
            self.assertNotIn("known_kitties", f["state"])
            self.assertEqual(len(f["state"]["hands"]), 4)
            if not f["state"]["kitty_public"]:
                self.assertIsNone(f["state"]["kitty"])
            if f["action"] and f["action"]["type"] == "bury":
                self.assertEqual(f["action"], {"type": "bury", "count": 8})
        # Full on-disk actions remain available to future offline audits.
        private = json.loads((self.directory / meta["filename"]).read_text(encoding="utf-8"))
        self.assertTrue(any(f["action"] and f["action"]["type"] == "bury" and
                            len(f["action"]["cards"]) == 8 for f in private["frames"]))

    def test_old_replay_reconstructs_bottom_from_last_burial_only_at_settlement(self):
        registry = self.registry()
        session = registry.enter({'mode': 'self', 'seed': 11,
                                  'config': {'initial_level': 11, 'auction': False}})
        room = registry.lookup(session['token'])
        with room.condition:
            self.finish(room)
        meta = registry.history.listing()['records'][0]
        path = self.directory / meta['filename']
        private = json.loads(path.read_text(encoding='utf-8'))
        private['frames'][-1]['state'].pop('settlement_kitty')
        burials = [f['action']['cards'] for f in private['frames']
                   if f['action'] and f['action']['type'] == 'bury']
        self.assertTrue(burials)
        path.write_text(json.dumps(private), encoding='utf-8')
        replay = HistoryStore(self.directory).read(meta['id'])
        self.assertEqual(replay['frames'][-1]['state']['settlement_kitty'], burials[-1])
        self.assertEqual(burials[-1], room.env.kitty)
        for f in replay['frames'][:-1]:
            self.assertNotIn('settlement_kitty', f['state'])

    def test_no_active_round_published_and_illegal_action_not_recorded(self):
        registry = self.registry();session = registry.enter({"mode": "self", "seed": 2})
        room = registry.lookup(session["token"])
        self.assertEqual(registry.history.listing()["records"], [])
        before = copy.deepcopy(room.recording.frames)
        with self.assertRaises(IllegalAction):
            room.step({"type": "play", "cards": [107]})
        self.assertEqual(before, room.recording.frames)
        for _ in range(15):
            room.step({"type": "pass"})
        self.assertEqual(registry.history.listing()["records"], [])

    def test_next_round_has_new_recording_and_save_is_idempotent(self):
        registry = self.registry();s = registry.enter({"mode": "self", "seed": 18})
        room = registry.lookup(s["token"]);self.finish(room)
        old_id,old_record = room.replay_id,room.recording
        self.assertEqual(registry.history.save(old_record,mode="self",room=room.code,
                            players=[],config={}), old_id)
        room.step({"type": "next_round"})
        self.assertIsNone(room.replay_id);self.assertEqual(len(room.recording.frames), 1)
        self.assertEqual(room.recording.frames[0]["state"]["round"], 2)
        self.finish(room,13)
        self.assertNotEqual(room.replay_id, old_id)
        self.assertEqual(len(registry.history.listing()["records"]), 2)

    def test_entropy_seed_snapshot_does_not_require_seed_reconstruction(self):
        registry = self.registry();s = registry.enter({"mode": "self"})
        room = registry.lookup(s["token"]);self.finish(room)
        replay = registry.history.read(room.replay_id)
        self.assertEqual(replay["meta"]["result"], room.env.result)
        self.assertEqual(replay["frames"][0]["state"]["hands"], room.recording.frames[0]["state"]["hands"])

    def test_no_bid_redeal_is_in_same_round_recording(self):
        registry = self.registry();s = registry.enter({"mode": "self", "seed": 20})
        room = registry.lookup(s["token"])
        for _ in range(99):room.step({"type": "pass"})
        self.assertTrue(self.skip_bidding_wait(room))
        self.assertEqual(room.env.redeals, 1)
        self.assertEqual(len(room.recording.frames), 101)
        self.assertTrue(any(e["type"] == "redeal" for e in room.recording.frames[-1]["events"]))
        self.assertEqual(registry.history.listing()["records"], [])
        self.finish(room)
        self.assertEqual(registry.history.read(room.replay_id)["frames"][-1]["state"]["redeals"], 1)

    def test_public_bottom_only_when_revealed_by_rule(self):
        env = TractorEnv(GameConfig(6,False,0), 77);rec = RoundRecording(env)
        for _ in range(100):
            actor=env.current_player;a={"type":"pass"};transition=env.step(a);rec.append(env,actor,a,transition)
        self.assertTrue(env.kitty_public)
        policy=RandomPolicy(19)
        while env.phase not in ("round_end","match_end"):
            actor=env.current_player;a=policy.act(env.observe(actor));transition=env.step(a);rec.append(env,actor,a,transition)
        store=HistoryStore(self.directory);identifier=store.save(rec,mode="self",room="test",players=[],config=asdict(env.config))
        self.assertEqual(store.read(identifier)["frames"][-1]["state"]["kitty"],env.kitty)

    def test_failed_throw_keeps_attempt_and_records_only_actual_cards_removed(self):
        registry=self.registry();session=registry.enter({'mode':'self','seed':34,'config':{'auction':False}})
        room=registry.lookup(session['token']);policy=RandomPolicy(98);failed_frame=None
        for _ in range(1000):
            if room.env.phase in ('round_end','match_end'):break
            if self.skip_bidding_wait(room):continue
            actor=room.env.current_player;before=room.env.hands[actor][:]
            action=policy.act(room.env.observe(actor))
            if room.env.phase=='playing' and not room.env.trick:
                groups=[[c for c in before if effective_suit(c,room.env.level,room.env.trump)==door]
                        for door in ('S','H','D','C','T')]
                cards=max(groups,key=len)[:7]
                if len(cards)%2==0:cards=cards[:-1]
                if len(cards)>=3:action={'type':'play','cards':cards}
            room.step(action)
            failed_frame=room.recording.frames[-1]
            event=next((e for e in failed_frame['events'] if e['type']=='throw_failed'),None)
            if event:
                self.assertEqual(failed_frame['action']['cards'],event['attempted'])
                self.assertEqual(set(failed_frame['state']['hands'][actor]),set(before)-set(event['cards']))
                self.assertEqual(len(event['attempted'])*10,event['penalty'])
                break
        else:self.fail('no failed throw reached')
        self.assertIsNotNone(event)
        self.finish(room)
        replay=registry.history.read(room.replay_id)
        self.assertIn(failed_frame,replay['frames'])

    def test_bot_final_action_is_recorded_and_saved(self):
        registry=self.registry();s=registry.enter({"mode":"self","seed":9})
        room=registry.lookup(s["token"]);policy=RandomPolicy(123)
        for _ in range(1000):
            if self.skip_bidding_wait(room):continue
            if room.env.phase=='playing' and len(room.env.trick)==3 and sum(map(len,room.env.hands))==len(room.env.trick[0]['cards']):break
            room.step(policy.act(room.env.observe(room.env.current_player)))
        else:self.fail('could not reach final action')
        with room.condition:room.autoplay=True;room.next_bot_at=0
        worker=threading.Thread(target=registry.run_bots,daemon=True);worker.start()
        try:
            deadline=time.monotonic()+3
            while not room.replay_id and time.monotonic()<deadline:time.sleep(.02)
            self.assertIsNotNone(room.replay_id)
            replay=registry.history.read(room.replay_id)
            self.assertEqual(replay['frames'][-1]['action']['type'],'play')
            self.assertEqual(replay['frames'][-1]['state']['result'],room.env.result)
        finally:registry.stopped.set();worker.join(2)

    def test_failed_write_does_not_undo_completed_game(self):
        registry=self.registry();s=registry.enter({'mode':'self','seed':15});room=registry.lookup(s['token'])
        with patch.object(registry.history,'save',side_effect=OSError('disk full')),patch('builtins.print'):
            self.finish(room)
        self.assertIn(room.env.phase,('round_end','match_end'));self.assertIsNone(room.replay_id)
        self.assertIsNotNone(room.history_error);self.assertFalse(room.policy_error)

    def test_bad_files_and_path_ids_cannot_escape_archive(self):
        self.directory.mkdir();(self.directory/'bad.json').write_text('{',encoding='utf-8')
        with self.assertLogs('tractor_sim.history',level='WARNING'):store=HistoryStore(self.directory)
        self.assertEqual(store.listing()['records'],[])
        for identifier in ('../secret','a'*31,'a'*32+'/../x',''):
            with self.assertRaises(KeyError):store.read(identifier)

    def test_http_actions_history_reads_and_restart(self):
        server=TractorServer(('127.0.0.1',0),policy_factory=RandomPolicy,history_dir=self.directory)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def req(path,data=None,token=None):
            request=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,
                headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
            try:
                with urllib.request.urlopen(request,timeout=10) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as error:return error.code,json.load(error)
        try:
            self.assertEqual(req('/api/history')[1]['records'],[])
            _,s=req('/api/session',{'mode':'self','seed':24,'name':'回放测试','config':{'initial_level':13,'auction':False}})
            token=s['token']
            policy=RandomPolicy(44)
            for _ in range(500):
                if s['state']['phase'] in ('round_end','match_end'):break
                if s['state'].get('bidding_window',{}).get('complete'):
                    room=server.registry.lookup(token)
                    with room.condition:room.bidding_deadline=time.monotonic()-1
                    _,s=req('/api/state',token=token)
                    continue
                status,s=req('/api/action',{'version':s['version'],'action':policy.act(s['state'])},token)
                self.assertEqual(status,200)
            else:self.fail('HTTP game did not finish')
            identifier=s['replay_id'];self.assertIsNotNone(identifier)
            status,listing=req('/api/history');self.assertEqual(status,200);self.assertEqual(len(listing['records']),1)
            status,replay=req('/api/history/'+identifier);self.assertEqual(status,200)
            self.assertEqual(replay['frames'][-1]['state']['result'],s['state']['result'])
            self.assertNotIn('token',json.dumps(replay));self.assertNotIn('deck',json.dumps(replay))
            self.assertEqual(req('/api/history/'+'a'*32)[0],404)
            self.assertEqual(req('/api/history/../engine.py')[0],404)
            self.assertEqual(req('/runtime/history/'+quote(listing['records'][0]['filename']))[0],404)
        finally:server.shutdown();server.server_close();worker.join(2)
        # A fresh registry reads the same records after the room/session expires.
        self.assertEqual(len(self.registry().history.listing()['records']),1)


if __name__=='__main__':unittest.main()
