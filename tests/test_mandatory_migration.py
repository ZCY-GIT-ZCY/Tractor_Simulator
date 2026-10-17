import copy
import json
import tempfile
import unittest
from pathlib import Path

from tractor_sim.engine import GameConfig, TractorEnv, MANDATORY_COMPLETION_RULE
from tractor_sim.checkpoint import load_registry
from tractor_sim.server import Registry
from test_rules import c, fixture


def recorded_results():
    # Public results of the reported eight-hand sequence, with no private cards.
    rows = [(2,0,70,[2,2],[3,2]), (3,2,65,[3,2],[4,2]),
            (4,0,35,[4,2],[5,2]), (5,2,50,[5,2],[6,2]),
            (6,0,140,[6,2],[6,2]), (2,3,130,[6,2],[7,2]),
            (7,2,70,[7,2],[8,2]), (8,0,150,[8,2],[8,3])]
    return [dict(seq=i, type='round_ended', round=i+1, level=level,
                 banker=banker, score=score, winner=banker%2 if score<80 else 1-banker%2,
                 levels_before=before, levels_after=after, upgrading_team=banker%2 if score<80 else 1-banker%2,
                 upgrade=1, mandatory_stop=None)
            for i,(level,banker,score,before,after) in enumerate(rows)]


def legacy_state():
    env=TractorEnv(GameConfig(2,False,0),seed=14)
    env.levels=[8,3];env.banker=3;env.round_no=8;env._start_round()
    started=env.events[-1];started['seq']=8
    env.events=recorded_results()+[started]
    env.passed=[{2,3,4,5,6,7,8},{2}]
    state=env.export_state();state.pop('mandatory_completion_rule')
    return state


class MandatoryMigration(unittest.TestCase):
    def test_reported_sequence_removes_losing_credit_and_repairs_next_level_only(self):
        state=legacy_state();before=copy.deepcopy(state)
        env=TractorEnv.from_state(state)
        self.assertEqual(env.passed,[{2,3,4,5,7},set()])
        self.assertEqual(env.levels,[8,2])
        self.assertEqual(env.level,3)  # Preserve the already-dealt hand's trump rank.
        for field in ('hands','deck','trump','current_player','deal_count','phase'):
            self.assertEqual(getattr(env,field),before[field])
        self.assertEqual(env.mandatory_completion_rule,MANDATORY_COMPLETION_RULE)
        self.assertEqual(state,before)
        checkpoint=env.export_state()
        self.assertEqual(TractorEnv.from_state(json.loads(json.dumps(checkpoint))).export_state(),checkpoint)

    def test_higher_initial_level_does_not_require_earlier_levels_until_a_downgrade(self):
        env=TractorEnv(GameConfig(8,False,0),seed=2)
        state=env.export_state();state.pop('mandatory_completion_rule')
        restored=TractorEnv.from_state(state)
        self.assertEqual(restored.levels,[8,8]);self.assertEqual(restored.passed,[set(),set()])
        state['events'].append(dict(type='round_ended',round=1,banker=0,level=11,
            winner=1,levels_after=[2,8]))
        state['levels']=[4,8]
        restored=TractorEnv.from_state(state)
        self.assertEqual(restored.levels,[2,8]);self.assertNotIn(11,restored.passed[0])

    def test_earlier_defending_win_remains_valid_after_a_later_defeat(self):
        state=legacy_state()
        state['events'].insert(0,dict(type='round_ended',round=0,banker=1,level=2,
                                    winner=1,levels_after=[2,3]))
        env=TractorEnv.from_state(state)
        self.assertIn(2,env.passed[1]);self.assertEqual(env.levels,[8,3])

    def test_pending_settlement_and_its_event_show_the_corrected_promotion(self):
        env=fixture([[53],c('S',3),c('S',4),c('S',5)],level=8)
        for _ in range(4):env.step({'type':'play','cards':env.hands[env.current_player][:]})
        state=env.export_state();state.pop('mandatory_completion_rule')
        state.update(config=dict(initial_level=2,auction=False,banker=0),round_no=8,
                     levels=[8,3],passed=[[2,3,4,5,6,7,8],[2]],events=recorded_results(),
                     score=150,captured_score=150,penalty_score=0,bottom_score=0)
        state['result'].update(copy.deepcopy(state['events'][-1]))
        state['result'].update(captured=150,bottom=0,multiplier=0)
        restored=TractorEnv.from_state(state)
        self.assertEqual(restored.result['levels_after'],[8,2])
        self.assertEqual(restored.result['mandatory_stop'],2)
        self.assertEqual(restored.events[-1]['levels_after'],[8,2])
        self.assertEqual(restored.events[-1]['mandatory_stop'],2)

    def test_inherited_active_ace_hand_cannot_finish_the_match_while_two_is_still_owed(self):
        env=fixture([[53],c('S',3),c('S',4),c('S',5)],level=14)
        state=env.export_state();state.pop('mandatory_completion_rule')
        state['config']['initial_level']=2
        state['events']=[dict(type='round_ended',round=0,banker=0,level=2,winner=1,levels_after=[2,2])]
        restored=TractorEnv.from_state(state)
        self.assertEqual((restored.level,restored.levels[0]),(14,2))
        for _ in range(4):restored.step({'type':'play','cards':restored.hands[restored.current_player][:]})
        self.assertEqual(restored.phase,'round_end');self.assertIsNone(restored.match_winner)
        self.assertEqual(restored.levels[0],2);self.assertEqual(restored.result['mandatory_stop'],2)

    def test_live_room_migration_keeps_credentials_and_cards(self):
        root=Path(__file__).resolve().parent.parent / 'build'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            registry=Registry(history_dir=Path(directory)/'history')
            session=registry.enter({'mode':'self','name':'迁移测试'})
            document=registry.freeze()
            document['rooms'][0]['env']=legacy_state()
            restored=Registry(history_dir=Path(directory)/'history')
            load_registry(restored,document)
            room=restored.lookup(session['token'])
            self.assertEqual(room.env.levels,[8,2]);self.assertEqual(room.env.passed[1],set())
            self.assertEqual(room.clients[session['token']]['name'],'迁移测试')
            self.assertEqual(room.env.hands,document['rooms'][0]['env']['hands'])
