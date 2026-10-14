import copy
import threading
import time
import unittest

from tractor_sim import GameConfig, IllegalAction
from tractor_sim.policy import RandomPolicy
from tractor_sim.server import Room, Registry, AI_TRICK_REVIEW_SECONDS
from test_rules import c, fixture, dealt_bidding_env
import test_table_review


class TableComfortTests(unittest.TestCase):
    def ai_room(self):
        room=Room('comfort', 'pvp', GameConfig(auction=False, banker=2), 18, RandomPolicy)
        tokens=[room.join('真人'+str(i)) for i in range(2)]
        with room.condition:
            for seat,token in enumerate(tokens): room.confirm_seat(token,seat)
            room.set_ai(tokens[0],None,True)
            room.start_room(tokens[0])
            test_table_review.TableReviewTests.finish(self,room)
        return room,tokens

    def test_ai_banker_requires_each_human_and_duplicate_ack_does_not_count_twice(self):
        room,tokens=self.ai_room()
        with room.condition:
            before=room.env.export_state();round_no=room.env.round_no
            room.confirm_result(tokens[0],round_no)
            room.confirm_result(tokens[0],round_no)
            self.assertEqual(room.env.export_state(),before)
            self.assertEqual(room.review_confirmations,[tokens[0]])
            state=room.snapshot(tokens[1])['settlement']
            self.assertTrue(state['can_confirm']);self.assertEqual(len(state['pending_humans']),1)
            self.assertFalse(room.snapshot(tokens[0])['settlement']['can_confirm'])
            room.confirm_result(tokens[1],round_no)
            self.assertEqual(room.env.round_no,round_no+1)
            with self.assertRaises(IllegalAction) as error: room.confirm_result(tokens[0],round_no)
            self.assertEqual(error.exception.code,'REVIEW_STALE')

    def test_waiting_member_is_not_required_and_cannot_ack(self):
        room,tokens=self.ai_room()
        observer=room.join('候场')
        with room.condition:
            self.assertEqual(len(room.snapshot(observer)['settlement']['humans']),2)
            self.assertFalse(room.snapshot(observer)['settlement']['can_confirm'])
            with self.assertRaises(IllegalAction):room.confirm_result(observer)

    def test_departing_unconfirmed_human_releases_the_review_barrier(self):
        room,tokens=self.ai_room()
        with room.condition:
            round_no=room.env.round_no;room.confirm_result(tokens[0],round_no)
            room.leave(tokens[1])
            self.assertEqual(room.env.round_no,round_no+1)

    def test_replacing_ai_banker_does_not_silently_change_review_to_banker_only(self):
        room,tokens=self.ai_room()
        newcomer=room.join('下一局庄家')
        with room.condition:
            room.confirm_seat(newcomer,2)
            review=room.snapshot(tokens[0])['settlement']
            self.assertTrue(review['ai_banker']);self.assertEqual(len(review['humans']),3)

    def test_paused_confirmations_allow_a_newly_seated_human_to_review_before_resuming(self):
        room,tokens=self.ai_room()
        with room.condition:
            room.policy_error=True;room.refresh_pause()
            for token in tokens:room.confirm_result(token,room.env.round_no)
            self.assertIsNone(room.review_confirmed_by)
            newcomer=room.join('新入座真人');room.confirm_seat(newcomer,2)
            self.assertTrue(room.snapshot(newcomer)['settlement']['can_confirm'])
            room.policy_error=False;room.refresh_pause()
            self.assertFalse(room.finish_ai_review())
            room.confirm_result(newcomer,room.env.round_no)
            self.assertEqual(room.env.round_no,2)

    def test_host_record_setting_is_room_wide_and_does_not_modify_engine_or_replays(self):
        room,tokens=self.ai_room()
        with room.condition:
            before=copy.deepcopy(room.env.export_state())
            with self.assertRaises(IllegalAction) as error: room.set_record_plays(tokens[1],True)
            self.assertEqual(error.exception.code,'HOST_ONLY')
            with self.assertRaises(ValueError):room.set_record_plays(tokens[0],1)
            room.set_record_plays(tokens[0],True)
            self.assertTrue(room.snapshot(tokens[1])['record_plays'])
            self.assertEqual(room.env.export_state(),before)
            room.set_record_plays(tokens[0],False)
            self.assertFalse(room.snapshot(tokens[0])['record_plays'])

    def test_current_bottom_is_private_and_only_marked_while_burying(self):
        env=dealt_bidding_env(banker=0,bidder=0,reserved=[c('H',2),[],[],c('D',2,True)])
        received=env.known_kitties[0][-1][:]
        self.assertEqual(env.observe(0)['received_kitty'],received)
        for seat in [1,2,3]:self.assertEqual(env.observe(seat)['received_kitty'],[])
        buried=env.hands[0][:8]
        env.step({'type':'bury','cards':buried})
        self.assertEqual(env.observe(0)['received_kitty'],[])
        env.step({'type':'bid','cards':c('D',2,True)},3)
        self.assertEqual(env.observe(3)['received_kitty'],buried)
        self.assertEqual(env.observe(0)['received_kitty'],[])

    def test_ai_next_lead_waits_two_seconds_without_delaying_human_lead(self):
        room=Room('pause','pve',GameConfig(auction=False),policy_factory=RandomPolicy)
        token=room.join('真人',0)
        room.env=fixture([c('S',3)+c('C',3),c('S',4)+c('C',4),c('S',14)+c('C',14),c('S',5)+c('C',5)],trump='H')
        with room.condition:
            for rank in [3,5,14,4]:room.step({'type':'play','cards':c('S',rank)})
            self.assertEqual(room.env.current_player,2)
            self.assertGreater(room.trick_review_remaining(),AI_TRICK_REVIEW_SECONDS-.1)
            registry=Registry(policy_factory=RandomPolicy);registry.rooms[room.code]=room
        thread=threading.Thread(target=registry.run_bots,daemon=True);thread.start()
        try:
            time.sleep(.1)
            with room.condition:
                self.assertEqual(room.env.trick,[])
                room.trick_review_until=time.monotonic()-.1;room.next_bot_at=0
            deadline=time.monotonic()+2
            while room.env.phase=='playing' and not room.env.trick and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(room.env.trick or room.env.phase=='round_end')
        finally:registry.stopped.set();thread.join(2)
        room=Room('humanpause','pve',GameConfig(auction=False),policy_factory=RandomPolicy)
        room.join('真人',0)
        room.env=fixture([c('S',14)+c('C',14),c('S',4)+c('C',4),c('S',3)+c('C',3),c('S',5)+c('C',5)],trump='H')
        with room.condition:
            for rank in [14,5,3,4]:room.step({'type':'play','cards':c('S',rank)})
            self.assertEqual(room.trick_review_remaining(),0)
            room.step({'type':'play','cards':c('C',14)})
            self.assertEqual(len(room.env.trick),1)


if __name__=='__main__':unittest.main()
