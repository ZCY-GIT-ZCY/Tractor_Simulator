"""Create a clean source deployment ZIP with bundled authoritative rules."""
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "dist" / "TractorWeb.zip"


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    files = []
    for folder in ("tractor_sim", "web", "tools", "tests", "licenses"):
        files += [p for p in (ROOT / folder).rglob("*") if p.is_file()
                  and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")]
    files += [ROOT / name for name in ("README.md", "启动双升联网游戏.bat", "停止双升联网服务.bat",
                                      "run.bat", "run.ps1", "run-public.bat", "run-lan.bat",
                                      "stop-public.ps1", "stop-public.bat", "internet.json", "pyproject.toml", "THIRD_PARTY.md")]
    files += [ROOT / "docs" / name for name in ("deployment.md", "internet.md", "api.md", "validation.md", "双升游戏规则.录音原版.md")]
    files += [ROOT / "docs" / name for name in ("ai.md", "ai-evaluation.md", "replay.md")]
    for name in ("ai-benchmark.json", "ai-http-validation.json", "ai-evaluation.json", "ai-rule-cases.json", "ai-manual-evaluation.json"):
        if (ROOT / "docs" / name).is_file():
            files.append(ROOT / "docs" / name)
    for name in ("history-validation.json", "replay-history-desktop.png", "replay-viewer-desktop.png",
                 "replay-history-mobile.png", "replay-viewer-mobile.png", "table-review-desktop.png",
                 "table-review-mobile.png", "table-review-pvp-confirmed.png", "table-review-validation.json",
                 "seating-desktop.png", "seating-mobile.png", "seating-validation.json",
                 "multiplayer-fixes-validation.json", "multiplayer-preselection.png",
                 "multiplayer-preselection-mobile.png", "auto-deal-paused.png",
                 "turn-and-follow-validation.json", "hand-fit-desktop.png", "hand-fit-mobile.png",
                 "auction-two-confirmation.png", "diamond-follow-two.png",
                 "realtime-bidding-desktop.png", "realtime-bidding-mobile.png",
                 "bidding-grace-desktop.png", "bidding-grace-mobile.png", "bidding-grace-validation.json",
                 "pvp-ai-takeover-desktop.png", "pvp-ai-takeover-mobile.png", "pvp-takeover-validation.json",
                 "auto-draw-clock-desktop.png", "ai-feed-and-draw-validation.json",
                 "ai-control-validation.json",
                 "frontend.png", "frontend-draw.png", "frontend-debug-reconnect.png",
                 "frontend-final-bidding.png", "frontend-pve-draw.png", "web-desktop-game.png",
                 "web-desktop-invite.png", "web-live-invite.png", "web-mobile-game.png",
                 "web-mobile-lobby.png", "trump-notice.png", "full-match-validation.json",
                 "internet-validation.json", "package-validation.json"):
        if (ROOT / "docs" / name).is_file():
            files.append(ROOT / "docs" / name)
    rules = ROOT.parent / "双升游戏规则.md"
    if not rules.is_file(): rules = ROOT / "rules" / "双升游戏规则.md"
    manifest = {}
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            relative = path.relative_to(ROOT).as_posix()
            contents = path.read_bytes()
            if relative == "README.md":
                contents = contents.decode("utf-8").replace(
                    "](../双升游戏规则.md)", "](rules/双升游戏规则.md)").encode("utf-8")
            archive.writestr("TractorWeb/" + relative, contents)
            manifest[relative] = hashlib.sha256(contents).hexdigest()
        contents = rules.read_bytes()
        archive.writestr("TractorWeb/rules/双升游戏规则.md", contents)
        manifest["rules/双升游戏规则.md"] = hashlib.sha256(contents).hexdigest()
        archive.writestr("TractorWeb/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"{OUTPUT}: {len(manifest)} files, {OUTPUT.stat().st_size} bytes")


if __name__ == "__main__": main()
