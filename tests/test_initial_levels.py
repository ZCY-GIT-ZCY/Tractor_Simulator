import json
import unittest
from dataclasses import asdict

from tractor_sim import GameConfig, TractorEnv
from test_rules import c, fixture


class IndependentInitialLevels(unittest.TestCase):
    def test_each_banker_uses_its_own_teams_level(self):
        for banker in range(4):
            with self.subTest(banker=banker):
                config=GameConfig(auction=False,banker=banker,initial_level_a=8,initial_level_b=2)
                env=TractorEnv(config,seed=73)
                self.assertEqual(env.levels,[8,2])
                self.assertEqual(env.level,8 if banker%2==0 else 2)
                self.assertEqual(env.current_player,banker)
                self.assertEqual(env.hands[banker],[env.deck[0]])
                self.assertEqual(env.passed,[set(),set()])

    def test_old_common_level_and_partial_overrides_remain_compatible(self):
        for auction in (False,True):
            env=TractorEnv(GameConfig(6,auction,0),seed=2)
            self.assertEqual((env.levels,env.level),([6,6],6))
        config=GameConfig(6,False,1,initial_level_b=8)
        self.assertEqual(config.starting_levels,[6,8])
        levels=config.starting_levels;levels[0]=2
        self.assertEqual(config.starting_levels,[6,8])

    def test_invalid_team_levels_and_overrides_during_auction_are_rejected(self):
        for field in ('initial_level_a','initial_level_b'):
            for value in (False,True,1,15,'8',8.0,[],{}):
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                    GameConfig(auction=False,**{field:value})
            with self.assertRaises(ValueError):GameConfig(auction=True,**{field:2})

    def test_gaining_the_table_uses_the_other_teams_initial_level_without_crediting_a_win(self):
        env=fixture([c('S',3),c('H',3),c('D',3),c('C',3)],level=8)
        env.config=GameConfig(auction=False,banker=0,initial_level_a=8,initial_level_b=2)
        env.levels=env.config.starting_levels;env.score=env.captured_score=150
        env.last_trick={'plays':[{'player':p,'cards':h} for p,h in enumerate(env.hands)]}
        env._settle(0)
        self.assertEqual(env.levels,[8,2]);self.assertEqual(env.result['mandatory_stop'],2)
        self.assertEqual(env.passed,[set(),set()])
        env.step({'type':'next_round'})
        self.assertEqual((env.banker,env.level),(3,2))

    def test_json_checkpoint_preserves_both_starts_and_legacy_migration_uses_each_floor(self):
        env=TractorEnv(GameConfig(auction=False,banker=0,initial_level_a=8,initial_level_b=2),seed=3)
        state=env.export_state()
        restored=TractorEnv.from_state(json.loads(json.dumps(state)))
        self.assertEqual(restored.export_state(),state)
        self.assertEqual(asdict(restored.config),asdict(env.config))
        state.pop('mandatory_completion_rule');state['levels']=[8,3]
        migrated=TractorEnv.from_state(state)
        self.assertEqual(migrated.levels,[8,2])
