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
    for folder in ("tractor_sim", "web", "tools", "tests"):
        files += [p for p in (ROOT / folder).rglob("*") if p.is_file()
                  and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")]
    files += [ROOT / name for name in ("README.md", "run.bat", "run.ps1", "run-public.bat", "run-lan.bat",
                                      "stop-public.ps1", "stop-public.bat", "internet.json", "pyproject.toml", "THIRD_PARTY.md")]
    files += [ROOT / "docs" / name for name in ("internet.md", "api.md", "validation.md", "双升游戏规则.录音原版.md")]
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
