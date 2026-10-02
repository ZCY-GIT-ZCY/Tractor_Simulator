import argparse
import errno
import json
import time
import webbrowser
import urllib.request
from pathlib import Path
from .engine import TractorEnv, GameConfig
from .policy import RandomPolicy


def existing_tractor(url, bind_host):
    """Recognize our server before reusing an occupied port; bypass proxies."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/api/health", timeout=1.5) as response:
            health = json.loads(response.read(4096))
        if not isinstance(health, dict) or health.get("ok") is not True or health.get("schema_version") != 1:
            return False
        actual_host = health.get("bind_host")
        normalize = lambda host: "127.0.0.1" if host == "localhost" else host
        if actual_host is not None and normalize(actual_host) != normalize(bind_host):
            return False
        if actual_host is None and bind_host not in ("127.0.0.1", "localhost"):
            return False
        if health.get("service") == "tractor_sim":
            return True
        if health.get("service") is not None:
            return False
        # Recognize an already-running version from before the service marker.
        with opener.open(url + "/", timeout=1.5) as response:
            return "<title>双升 · 牌桌</title>" in response.read(65536).decode("utf-8")
    except (OSError, ValueError, UnicodeError):
        return False


def main():
    parser = argparse.ArgumentParser(description="双升模拟器：默认打开浏览器；--headless 运行纯仿真")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--serve", action="store_true", help="配合 --headless 运行无浏览器服务器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--level", type=int, default=2, help="2..14，J=11 K=13 A=14")
    parser.add_argument("--no-auction", action="store_true")
    parser.add_argument("--banker", type=int, default=0)
    parser.add_argument("--full-match", action="store_true", help="每个 episode 为完整升级比赛")
    parser.add_argument("--output", type=Path, help="保存 JSON 统计与可重放初始种子、动作日志")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--replay", type=Path, help="校验 --output 保存的 JSON 中全部 episode")
    args = parser.parse_args()
    if args.replay:
        from .replay import replay_episode
        document = json.loads(args.replay.read_text(encoding="utf-8"))
        episodes = document["episodes"]
        rounds = sum(replay_episode(e).round_no for e in episodes)
        print(json.dumps({"verified_episodes": len(episodes), "verified_rounds": rounds}, ensure_ascii=False))
        return
    if not args.headless or args.serve:
        from .server import TractorServer
        browser_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
        url = f"http://{browser_host}:{args.port}"
        try:
            server = TractorServer((args.host, args.port), verbose=args.verbose)
        except OSError as exc:
            occupied = exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) in (10048, 10013)
            if occupied and existing_tractor(url, args.host):
                print(f"牌桌服务已在运行：{url}，继续使用已有服务。", flush=True)
                if not args.headless:
                    webbrowser.open(url)
                return
            if occupied:
                parser.exit(1, f"端口 {args.port} 已被占用，未找到相同监听地址的双升服务。\n"
                               f"请使用其他端口，例如：python -m tractor_sim --port {args.port + 1}\n")
            parser.exit(1, f"无法启动牌桌服务：{exc}\n")
        url = f"http://{browser_host}:{server.server_port}"
        print(f"Tractor Simulator: {url}   Ctrl+C to stop", flush=True)
        if not args.headless:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    if args.episodes < 1:
        parser.error("episodes must be >=1")
    started = time.perf_counter()
    results = []
    config = GameConfig(args.level, not args.no_auction, args.banker)
    for i in range(args.episodes):
        env = TractorEnv(config, args.seed + i)
        policy = RandomPolicy(args.seed + i + 100000)
        steps = 0
        while True:
            if env.phase == "match_end" or (env.phase == "round_end" and not args.full_match):
                break
            env.step(policy.act(env.observe(env.current_player))); steps += 1
            if steps > 200000:
                raise RuntimeError("异常长比赛，请检查日志")
        results.append({"seed": args.seed + i, "config": vars(config), "steps": steps,
                        "rounds": env.round_no, "result": env.result, "actions": env.action_log})
    seconds = time.perf_counter() - started
    stats = {"episodes": args.episodes, "rounds": sum(r["rounds"] for r in results),
             "steps": sum(r["steps"] for r in results), "seconds": seconds,
             "steps_per_second": sum(r["steps"] for r in results) / max(seconds, 1e-9)}
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"stats": stats, "episodes": results}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
