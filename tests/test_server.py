import json
import threading
import time
import unittest
import urllib.request
import urllib.error
import tempfile
from pathlib import Path
from tractor_sim.server import TractorServer
from tractor_sim.policy import RandomPolicy
from tractor_sim.rules import IllegalAction
from tractor_sim.server import Registry


class RoomAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory=Path(__file__).resolve().parent.parent / 'build'
        directory.mkdir(exist_ok=True)
        cls.tmp=tempfile.TemporaryDirectory(dir=directory)
        cls.server=TractorServer(('127.0.0.1',0), history_dir=Path(cls.tmp.name) / 'history')
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(2)
        cls.tmp.cleanup()

    def request(self,path,data=None,token=None):
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,
                                   headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        try:
            with urllib.request.urlopen(req,timeout=10) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)

    def seated_room(self, code, config=None, seed=None):
        clients = [self.request('/api/session', {'mode': 'pvp', 'room': code,
            'config': config or {}, 'seed': seed, 'name': 'p' + str(i)})[1] for i in range(4)]
        for i, client in enumerate(clients):
            status, state = self.request('/api/seat/confirm', {'seat': i}, client['token'])
            self.assertEqual(status, 200)
        self.assertEqual(self.request('/api/room/start', {}, clients[0]['token'])[0], 200)
        return clients

    def test_four_confirmed_seats_host_start_and_privacy(self):
        clients=[]
        for i in range(4):
            status,s=self.request('/api/session',{'mode':'pvp','room':'test_four','name':f'p{i}'})
            self.assertEqual(status,200);self.assertIsNone(s['seat']);self.assertIsNone(s['state'])
            session_token=s['token']
            status,s=self.request('/api/seat/confirm',{'seat':i},s['token'])
            self.assertEqual(status,200);self.assertEqual(s['seat'],i);s['token']=session_token;clients.append(s)
        self.assertFalse(clients[-1]['started'])
        self.assertEqual(self.request('/api/room/start',{},clients[0]['token'])[0],200)
        status,state=self.request('/api/state',token=clients[0]['token'])
        self.assertNotIn('hands',state['state']);self.assertIsNone(state['state']['kitty'])
        self.assertNotIn('deck',state['state'])
        self.assertEqual(state['controlled'],[0])
        status,bad=self.request('/api/action',{'version':state['version'],'action':{'type':'pass'}},clients[1]['token'])
        self.assertEqual(status,422);self.assertEqual(bad['code'],'NOT_YOUR_TURN')
        status,_=self.request('/api/action',{'version':state['version'],'action':{'type':'pass'}},clients[0]['token'])
        self.assertEqual(status,200)
        _,state=self.request('/api/state',token=clients[1]['token'])
        status,_=self.request('/api/action',{'version':state['version'],'action':{'type':'random'}},clients[1]['token'])
        self.assertEqual(status,422)
        status, delegated = self.request('/api/autoplay',{'enabled':True},clients[1]['token'])
        self.assertEqual(status,200);self.assertTrue(delegated['takeover'])
        self.assertFalse(delegated['autoplay'])
        self.assertEqual(self.request('/api/autoplay',{'enabled':False},clients[1]['token'])[0],200)
        status,waiting=self.request('/api/session',{'mode':'pvp','room':'test_four','name':'fifth'})
        self.assertEqual(status,422);self.assertEqual(waiting['code'],'IN_PROGRESS')

    def test_stale_action_atomic_and_auth(self):
        _,s=self.request('/api/session',{'mode':'self','seed':3})
        status,_=self.request('/api/action',{'version':-1,'action':{'type':'pass'}},s['token'])
        self.assertEqual(status,409)
        _,after=self.request('/api/state',token=s['token']);self.assertEqual(after['state']['deal_count'],1)
        self.assertEqual(self.request('/api/state')[0],403)

    def test_self_and_pve_visibility(self):
        _,s=self.request('/api/session',{'mode':'self'})
        self.assertIn('hands',s['state']);self.assertEqual(s['controlled'],list(range(4)))
        self.assertIsNone(s['state']['kitty'])
        _,p=self.request('/api/session',{'mode':'pve','seat':0})
        self.assertNotIn('hands',p['state']);self.assertEqual(sum(x['bot'] for x in p['players']),3)

    def realtime_room(self, code):
        from test_realtime_bidding import waiting_deal
        clients=self.seated_room(code)
        room=self.server.registry.lookup(clients[0]['token'])
        with room.condition:
            room.env=waiting_deal();room.bump()
        _,snapshot=self.request('/api/state',token=clients[0]['token'])
        return clients,room,snapshot

    def test_realtime_bids_use_arrival_order_not_snapshot_version(self):
        from test_rules import c
        clients,room,s=self.realtime_room('realtime_order')
        body={'round':1,'redeals':0,'version':s['version'],'action':{'type':'bid','cards':c('H',2)}}
        status,after=self.request('/api/bid',body,clients[0]['token'])
        self.assertEqual(status,200);self.assertEqual(after['state']['current_player'],1)
        self.assertEqual(after['state']['deal_count'],100)
        # The same earlier version can submit a higher bid; its sender is not
        # the current drawer. Validation must also tolerate draw/bid revisions.
        body['action']={'type':'bid','cards':c('S',2,True)}
        status,v=self.request('/api/validate',{**body,'context':{'level':2,'trump':None}},clients[1]['token'])
        self.assertEqual(status,200);self.assertTrue(v['valid'])
        status,after=self.request('/api/bid',body,clients[1]['token'])
        self.assertEqual(status,200);self.assertEqual(after['state']['bid']['player'],1)
        body['action']={'type':'bid','cards':c('H',2,True)}
        before=room.env.export_state();version=room.version
        status,bad=self.request('/api/bid',body,clients[0]['token'])
        self.assertEqual(status,422);self.assertEqual(bad['code'],'BID_TOO_WEAK')
        self.assertIn('亮主无效',bad['message'])
        self.assertEqual(room.env.export_state(),before);self.assertEqual(room.version,version)
        body['action']={'type':'bid','cards':[53,107]}
        status,after=self.request('/api/bid',body,clients[2]['token'])
        self.assertEqual(status,200);self.assertEqual(after['state']['bid']['player'],2)
        self.assertEqual(after['state']['deal_count'],100)

    def test_concurrent_equal_bids_only_accept_first_request(self):
        from test_rules import c
        clients,room,s=self.realtime_room('realtime_race')
        barrier=threading.Barrier(2);results=[]
        def bid(seat):
            barrier.wait()
            results.append((seat,self.request('/api/bid',{'round':1,'redeals':0,'version':s['version'],
                'action':{'type':'bid','cards':c('H' if seat==0 else 'S',2)}},clients[seat]['token'])))
        workers=[threading.Thread(target=bid,args=(seat,)) for seat in (0,1)]
        for t in workers:t.start()
        for t in workers:t.join(10);self.assertFalse(t.is_alive())
        self.assertEqual(sorted(status for seat,(status,data) in results),[200,422])
        accepted=next(seat for seat,(status,data) in results if status==200)
        self.assertEqual(room.env.bid['player'],accepted);self.assertEqual(len(room.env.bid_history),1)
        self.assertEqual((room.env.current_player,room.env.deal_count),(1,100))

    def test_realtime_bid_cannot_cross_phase_round_or_own_seat(self):
        from test_rules import c
        clients,room,s=self.realtime_room('realtime_guards')
        body={'round':1,'redeals':0,'action':{'type':'bid','cards':c('H',2)}}
        before=room.env.export_state()
        self.assertEqual(self.request('/api/bid',{**body,'redeals':1},clients[0]['token'])[0],409)
        self.assertEqual(self.request('/api/bid',{**body,'round':2},clients[0]['token'])[0],409)
        status,bad=self.request('/api/bid',body,clients[3]['token'])
        self.assertEqual((status,bad['code']),(422,'NOT_OWNED'))
        self.assertEqual(room.env.export_state(),before)
        self.assertEqual(self.request('/api/bid',body,clients[0]['token'])[0],200)
        # Even the last drawer cannot end the six-second window early.
        self.assertEqual(self.request('/api/action',{'version':room.version,
            'action':{'type':'pass'}},clients[1]['token'])[0],422)
        with room.condition:room.bidding_deadline=time.monotonic()-1
        body['action']={'type':'bid','cards':[53,107]}
        status,bad=self.request('/api/bid',body,clients[2]['token'])
        self.assertEqual((status,bad['code']),(422,'BIDDING_CLOSED'))
        status,bad=self.request('/api/action',{'version':room.version,'action':body['action']},clients[2]['token'])
        self.assertEqual((status,bad['code']),(422,'NOT_YOUR_TURN'))

    def test_validate_binds_to_version_and_authoritative_level(self):
        from test_rules import c, fixture
        _,session=self.request('/api/session',{'mode':'self'})
        token=session['token'];room=self.server.registry.lookup(token)
        with room.condition:
            room.env=fixture([c('D',5),c('C',8),c('S',9),c('D',8)+[53]],level=2,trump='H')
            room.bump()
        _,before=self.request('/api/state',token=token)
        _,after=self.request('/api/action',{'version':before['version'],
            'action':{'type':'play','cards':c('D',5)}},token)
        status,valid=self.request('/api/validate',{'version':after['version'],
            'action':{'type':'play','cards':c('D',8)}},token)
        self.assertEqual(status,200);self.assertTrue(valid['valid'])
        self.assertEqual((valid['version'],valid['level'],valid['trump']),(after['version'],2,'H'))
        _,bad=self.request('/api/validate',{'version':after['version'],
            'action':{'type':'play','cards':[53]}},token)
        self.assertEqual(bad['code'],'FOLLOW_SUIT')
        status,stale=self.request('/api/validate',{'version':before['version'],
            'action':{'type':'play','cards':c('D',8)}},token)
        self.assertEqual(status,409);self.assertEqual(stale['error'],'STALE')

        # A displayed level mismatch is rejected even with the current version.
        before_env=room.env.export_state()
        status,mismatch=self.request('/api/validate',{'version':after['version'],
            'context':{'level':5,'trump':'H'},'action':{'type':'play','cards':c('D',8)}},token)
        self.assertEqual(status,409);self.assertEqual(mismatch['error'],'STALE')
        status,mismatch=self.request('/api/validate',{'version':after['version'],
            'context':{'level':2,'trump':'D'},'action':{'type':'play','cards':c('D',8)}},token)
        self.assertEqual(status,409);self.assertEqual(mismatch['error'],'STALE')
        self.assertEqual(room.env.export_state(),before_env)

        # Repeated invalid selections are recorded once, without other hands
        # or credentials, and never appear in the public replay collection.
        for _ in range(2):
            self.request('/api/validate',{'version':after['version'],
                'context':{'level':2,'trump':'H'},'action':{'type':'play','cards':[53]}},token)
        paths=list((Path(self.tmp.name) / 'diagnostics').glob('*.jsonl'))
        entries=[json.loads(line) for path in paths for line in path.read_text(encoding='utf-8').splitlines()]
        entries=[e for e in entries if e['room']==room.code]
        self.assertEqual(len(entries),1)
        self.assertEqual((entries[0]['level'],entries[0]['trump'],entries[0]['lead']),(2,'H',c('D',5)))
        self.assertEqual(entries[0]['selected'],[53])
        self.assertNotIn('token',entries[0]);self.assertNotIn('hands',entries[0]);self.assertNotIn('kitty',entries[0])

    def test_reconnect_keeps_hand(self):
        _,s=self.request('/api/session',{'mode':'self','seed':5})
        _,a=self.request('/api/state',token=s['token']);_,b=self.request('/api/state',token=s['token'])
        self.assertEqual(a['state']['hand'],b['state']['hand']);self.assertEqual(a['seat'],b['seat'])

    def test_static_and_path_traversal(self):
        with urllib.request.urlopen(self.url+'/') as r:self.assertIn('双升'.encode(),r.read())
        self.assertEqual(self.request('/%2e%2e/tractor_sim/engine.py')[0],404)

    def test_second_server_cannot_share_live_port(self):
        with self.assertRaises(OSError):TractorServer(self.server.server_address)

    def test_malformed_action_rejected_atomically(self):
        _,s=self.request('/api/session',{'mode':'self'})
        for action in ([],None,'pass',4):
            status,res=self.request('/api/validate',{'action':action},s['token'])
            self.assertEqual(status,422);self.assertEqual(res['code'],'ACTION')
        _,after=self.request('/api/state',token=s['token'])
        self.assertEqual(after['version'],s['version'])

    def test_pvp_disconnect_pause_and_same_seat_resume(self):
        clients=self.seated_room('disconnect_test')
        room=self.server.registry.lookup(clients[0]['token'])
        with room.condition:room.clients[clients[3]['token']]['seen']=time.monotonic()-30
        deadline=time.monotonic()+2
        while not room.paused and time.monotonic()<deadline:time.sleep(.02)
        self.assertTrue(room.paused)
        status,_=self.request('/api/action',{'version':room.version,'action':{'type':'pass'}},clients[0]['token'])
        self.assertEqual(status,422)
        _,res=self.request('/api/state',token=clients[3]['token']);self.assertEqual(res['seat'],3)
        deadline=time.monotonic()+2
        while room.paused and time.monotonic()<deadline:time.sleep(.02)
        self.assertFalse(room.paused)

    def test_only_host_can_start_next_round_even_when_other_seat_has_turn(self):
        clients = self.seated_room('host_gate', {'auction': False, 'banker': 0}, 18)
        room = self.server.registry.lookup(clients[0]['token'])
        policy = RandomPolicy(19)
        with room.condition:
            for _ in range(1000):
                room.env.step(policy.act(room.env.observe(room.env.current_player)))
                if room.env.phase == 'round_end': break
            else: self.fail('The test round did not finish')
            room.bump()
            version = room.version
            actor = room.env.current_player
            self.assertNotEqual(actor, 0)
            before = room.env.export_state()
        action = {'version': version, 'action': {'type': 'next_round'}}
        for path in ('/api/validate', '/api/action'):
            status, res = self.request(path, action, clients[actor]['token'])
            self.assertEqual(status, 422); self.assertEqual(res['code'], 'HOST_ONLY')
        self.assertEqual(room.env.export_state(), before)
        status, res = self.request('/api/action', action, clients[0]['token'])
        self.assertEqual(status, 422); self.assertEqual(res['code'], 'REVIEW_PENDING')
        # Actual round banker confirms; current_player already means next banker.
        status, res = self.request('/api/confirm-result', {'version': version}, clients[actor]['token'])
        self.assertEqual(status, 422); self.assertEqual(res['code'], 'BANKER_ONLY')
        status, res = self.request('/api/confirm-result', {'version': version}, clients[0]['token'])
        self.assertEqual(status, 200); self.assertTrue(res['settlement']['confirmed'])
        action['version'] = res['version']
        status, res = self.request('/api/action', action, clients[0]['token'])
        self.assertEqual(status, 200); self.assertEqual(res['state']['round'], 2)

    def test_connectivity_is_checked_without_waiting_for_bot_tick(self):
        registry = Registry()
        clients = [registry.enter({'mode': 'pvp', 'room': 'immediate_pause'}) for _ in range(4)]
        room = registry.lookup(clients[0]['token'])
        with room.condition:
            for i, client in enumerate(clients): room.confirm_seat(client['token'], i)
            room.start_room(clients[0]['token'])
            room.clients[clients[3]['token']]['seen'] -= 30
            before = room.env.export_state()
            with self.assertRaises(IllegalAction) as error:
                room.can_act(clients[0]['token'], {'type': 'pass'})
            self.assertEqual(error.exception.code, 'PAUSED')
            self.assertEqual(before, room.env.export_state())
            room.check(clients[3]['token'])  # HTTP transport records the reconnect.
            room.snapshot(clients[3]['token'])
            self.assertFalse(room.paused)
            del room.clients[clients[3]['token']]
            with self.assertRaises(IllegalAction) as error:
                room.can_act(clients[0]['token'], {'type': 'pass'})
            self.assertEqual(error.exception.code, 'PAUSED')

    def test_leave_transfers_host_and_automatically_delegates_without_mid_hand_join(self):
        clients = self.seated_room('host_leaves')
        _, before = self.request('/api/state', token=clients[0]['token'])
        self.assertEqual(self.request('/api/leave', {}, clients[0]['token'])[0], 200)
        _, remaining = self.request('/api/state', token=clients[1]['token'])
        self.assertTrue(remaining['host']); self.assertFalse(remaining['paused'])
        self.assertTrue(remaining['players'][0]['bot'])
        status, error = self.request('/api/session', {'mode': 'pvp', 'room': 'host_leaves'})
        self.assertEqual((status, error['code']), (422, 'IN_PROGRESS'))
        self.assertEqual(self.request('/api/leave', {}, clients[0]['token'])[0], 200)
        self.assertEqual(self.request('/api/state', token=clients[0]['token'])[0], 403)

    def test_autoplay_does_not_treat_string_false_as_true(self):
        _, session = self.request('/api/session', {'mode': 'self'})
        status, _ = self.request('/api/autoplay', {'enabled': 'false'}, session['token'])
        self.assertEqual(status, 400)
        _, after = self.request('/api/state', token=session['token'])
        self.assertFalse(after['autoplay']); self.assertEqual(after['version'], session['version'])


if __name__=='__main__':unittest.main()
