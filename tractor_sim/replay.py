"""Replay accepted actions, without depending on the policy which produced them."""
import copy
from .engine import TractorEnv


def replay_episode(episode):
    """Load one episode in the CLI JSON format and verify its recorded outcome.

    Explicit seed is required; for entropy-seeded games use export_state instead.
    Action logs and full checkpoints are trusted offline data, never observations.
    """
    if episode.get("seed") is None:
        raise ValueError("重放须有明确初始种子；无种子对局请保存完整状态")
    env = TractorEnv(episode["config"], episode["seed"])
    for index, record in enumerate(episode["actions"]):
        if record["round"] != env.round_no or record["player"] != env.current_player:
            raise ValueError(f"动作 {index} 的局号或行动座位不匹配")
        env.step(copy.deepcopy(record["action"]), record["player"])
    if "result" in episode:
        expected = copy.deepcopy(episode["result"])
        # Keep verifying pre-fix logs without publishing their hidden bottom.
        if not env.kitty_public and expected.get("kitty") is not None:
            if expected["kitty"] != env.kitty:
                raise ValueError("重放隐藏底牌与旧记录不一致")
            expected["kitty"] = None
        if expected != env.result:
            raise ValueError("重放结果与记录不一致")
    return env
