import json
import threading
import time
import unittest
import urllib.request
import urllib.error
from tractor_sim.server import TractorServer
from tractor_sim.policy import RandomPolicy
from tractor_sim.rules import IllegalAction
from tractor_sim.server import Registry


class RoomAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=TractorServer(('127.0.0.1',0))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(2)

    def request(self,path,data=None,token=None):
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,
                                   headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        try:
            with urllib.request.urlopen(req,timeout=10) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)

    def test_four_seats_autostart_and_privacy(self):
        clients=[]
        for i in range(4):
            status,s=self.request('/api/session',{'mode':'pvp','room':'test_four','name':f'p{i}'})
            self.assertEqual(status,200);self.assertEqual(s['seat'],i);clients.append(s)
        self.assertTrue(clients[-1]['started'])
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
        self.assertEqual(self.request('/api/autoplay',{'enabled':True},clients[1]['token'])[0],422)
        status,_=self.request('/api/session',{'mode':'pvp','room':'test_four','name':'fifth'})
        self.assertEqual(status,422)

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
        clients=[self.request('/api/session',{'mode':'pvp','room':'disconnect_test','name':str(i)})[1] for i in range(4)]
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
        clients = [self.request('/api/session', {'mode': 'pvp', 'room': 'host_gate',
                   'config': {'auction': False, 'banker': 0}, 'seed': 18})[1] for _ in range(4)]
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
        self.assertEqual(status, 200); self.assertEqual(res['state']['round'], 2)

    def test_connectivity_is_checked_without_waiting_for_bot_tick(self):
        registry = Registry()
        clients = [registry.enter({'mode': 'pvp', 'room': 'immediate_pause'}) for _ in range(4)]
        room = registry.lookup(clients[0]['token'])
        with room.condition:
            room.clients[clients[3]['token']]['seen'] -= 30
            before = room.env.export_state()
            with self.assertRaises(IllegalAction) as error:
                room.can_act(clients[0]['token'], {'type': 'pass'})
            self.assertEqual(error.exception.code, 'PAUSED')
            self.assertEqual(before, room.env.export_state())
            room.snapshot(clients[3]['token'])
            self.assertFalse(room.paused)
            del room.clients[clients[3]['token']]
            with self.assertRaises(IllegalAction) as error:
                room.can_act(clients[0]['token'], {'type': 'pass'})
            self.assertEqual(error.exception.code, 'PAUSED')

    def test_leave_transfers_host_and_replacement_keeps_existing_hand(self):
        clients = [self.request('/api/session', {'mode': 'pvp', 'room': 'host_leaves'})[1] for _ in range(4)]
        _, before = self.request('/api/state', token=clients[0]['token'])
        self.assertEqual(self.request('/api/leave', {}, clients[0]['token'])[0], 200)
        _, remaining = self.request('/api/state', token=clients[1]['token'])
        self.assertTrue(remaining['host']); self.assertTrue(remaining['paused'])
        _, replacement = self.request('/api/session', {'mode': 'pvp', 'room': 'host_leaves'})
        self.assertEqual(replacement['seat'], 0)
        self.assertFalse(replacement['host']); self.assertFalse(replacement['paused'])
        self.assertEqual(replacement['state']['hand'], before['state']['hand'])

    def test_autoplay_does_not_treat_string_false_as_true(self):
        _, session = self.request('/api/session', {'mode': 'self'})
        status, _ = self.request('/api/autoplay', {'enabled': 'false'}, session['token'])
        self.assertEqual(status, 400)
        _, after = self.request('/api/state', token=session['token'])
        self.assertFalse(after['autoplay']); self.assertEqual(after['version'], session['version'])


if __name__=='__main__':unittest.main()
