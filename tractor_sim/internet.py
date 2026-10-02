"""Local web server plus an outbound HTTPS tunnel; no system installation."""
import argparse
import ctypes
import errno
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timezone

from .server import ROOT, TractorServer

VERSION = "2026.9.3"
WINDOWS_SHA256 = "f096265ec2fcbe9bb6e2d64268db167ced3fcbb83d894bdb9e2fcdb26f2ea7e2"
DOWNLOAD_URL = f"https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/cloudflared-windows-amd64.exe"
DEPENDENCIES = ROOT.parent / "dependencies" / "cloudflared"
RUNTIME = ROOT / "runtime"
LAUNCH_STARTED = datetime.now(timezone.utc)
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class LauncherLease:
    """OS file lock prevents duplicate tunnels and releases even after a crash."""
    def __init__(self, runtime=RUNTIME):
        runtime.mkdir(parents=True, exist_ok=True)
        self.handle = (runtime / "public.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN) or getattr(exc, "winerror", None) == 33:
                raise BlockingIOError("另一启动器持有公网服务锁") from exc
            raise

    def close(self):
        self.handle.close()


def valid_public_url(value):
    parsed = urlparse(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("public_url 须为完整 HTTPS 域名入口，例如 https://tractor.example.com")
    return value.rstrip("/")


def validate_config(config, port=None):
    if not isinstance(config, dict):
        raise ValueError("公网配置须为 JSON 对象")
    mode = config.get("mode", "quick")
    if mode not in ("quick", "named"):
        raise ValueError("mode 须为 quick 或 named")
    port = config.get("port", 8765) if port is None else port
    if type(port) is not int or not 0 <= port <= 65535 or (mode == "named" and port == 0):
        raise ValueError("port 须为 0..65535 的整数；固定隧道不能使用随机端口 0")
    if type(config.get("max_rooms", 32)) is not int or config.get("max_rooms", 32) < 1:
        raise ValueError("max_rooms 须为正整数")
    if config.get("protocol", "http2") not in ("http2", "quic", "auto"):
        raise ValueError("protocol 须为 http2、quic 或 auto")
    if mode == "named":
        valid_public_url(config.get("public_url", ""))
    return port


def install_cloudflared():
    if platform.system() != "Windows" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise ValueError("自动下载支持 Windows x64；其他系统请下载 cloudflared 并用 --cloudflared 指定路径")
    DEPENDENCIES.mkdir(parents=True, exist_ok=True)
    binary = DEPENDENCIES / "cloudflared.exe"
    if binary.is_file() and hashlib.sha256(binary.read_bytes()).hexdigest() == WINDOWS_SHA256:
        return binary
    temporary = DEPENDENCIES / "cloudflared.exe.part"
    print(f"下载 Cloudflare 官方 cloudflared {VERSION}（约 53 MB），保存至 {DEPENDENCIES}", flush=True)
    try:
        digest = hashlib.sha256()
        request = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "TractorSimulator"})
        with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != WINDOWS_SHA256:
            raise ValueError("cloudflared SHA256 校验失败，请重试；没有执行下载文件")
        temporary.replace(binary)
        (DEPENDENCIES / "download.json").write_text(json.dumps({"version": VERSION, "url": DOWNLOAD_URL,
                          "sha256": WINDOWS_SHA256}, indent=2), encoding="utf-8")
        return binary
    finally:
        temporary.unlink(missing_ok=True)


def kill_child_on_windows_exit(process):
    """Windows closes this job handle if the launcher/window dies unexpectedly."""
    if os.name != "nt":
        return None
    from ctypes import wintypes
    class BasicLimits(ctypes.Structure):
        _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                    ("Flags", wintypes.DWORD), ("MinWorking", ctypes.c_size_t),
                    ("MaxWorking", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]
    class Limits(ctypes.Structure):
        _fields_ = [("Basic", BasicLimits), ("IO", IO), ("ProcessMemory", ctypes.c_size_t),
                    ("JobMemory", ctypes.c_size_t), ("PeakProcess", ctypes.c_size_t), ("PeakJob", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = Limits(); limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(process._handle))):
        error = ctypes.get_last_error()
        if handle: kernel.CloseHandle(handle)
        raise OSError(error, "无法设置隧道随启动器退出，请检查 Windows 进程权限")
    return kernel, handle


class Tunnel:
    def __init__(self, binary, local_url, config, runtime=RUNTIME):
        self.runtime = runtime
        self.process = self.reader = self.job = self.log = self.token_copy = None
        runtime.mkdir(parents=True, exist_ok=True)
        empty_config = runtime / "cloudflared-empty.yml"
        empty_config.write_text("{}\n", encoding="utf-8")
        command = [str(binary), "tunnel", "--config", str(empty_config), "--no-autoupdate",
                   "--protocol", config.get("protocol", "http2"), "--edge-ip-version", "4"]
        self.url = None
        if config.get("mode", "quick") == "named":
            self.url = valid_public_url(config.get("public_url", ""))
            token_file = Path(config.get("token_file", "runtime/tunnel-token.txt"))
            if not token_file.is_absolute(): token_file = ROOT / token_file
            credential = token_file.read_text(encoding="utf-8-sig").strip() if token_file.is_file() else ""
            if not credential or any(c.isspace() for c in credential):
                raise ValueError(f"请将固定隧道的 token 保存到 {token_file}，不要粘贴到聊天或网址中")
            # PowerShell/Notepad may add a BOM or a newline; cloudflared needs
            # the token itself. Keep the original untouched and remove this copy.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=runtime,
                                             prefix="tunnel-token-", suffix=".txt", delete=False) as normalized:
                self.token_copy = Path(normalized.name)
                normalized.write(credential)
            command += ["run", "--token-file", str(self.token_copy)]
        elif config.get("mode", "quick") == "quick":
            command += ["--url", local_url]
        else:
            raise ValueError("mode 须为 quick 或 named")
        self.connected = threading.Event()
        try:
            self.log = (runtime / "tunnel.log").open("w", encoding="utf-8")
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace", cwd=str(runtime),
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.job = kill_child_on_windows_exit(self.process)
            self.reader = threading.Thread(target=self.read_output, daemon=True, name="tractor-tunnel-log")
            self.reader.start()
        except BaseException:
            self.close()
            raise

    def read_output(self):
        for line in self.process.stdout:
            # cloudflared settings may contain credentials for a named tunnel.
            line = re.sub(r"(?i)(token[:= ]+)[^\s\],}]+", r"\1[redacted]", line)
            self.log.write(line); self.log.flush()
            found = URL_PATTERN.search(line)
            if found: self.url = found.group()
            if "Registered tunnel connection" in line: self.connected.set()

    def close(self):
        try:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(8)
                except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(5)
            if self.reader and self.reader.ident is not None:
                self.reader.join(2)
        finally:
            if self.process and self.process.stdout: self.process.stdout.close()
            if self.log: self.log.close()
            if self.token_copy: self.token_copy.unlink(missing_ok=True)
            if self.job:
                kernel, handle = self.job
                kernel.CloseHandle(handle)
                self.job = None


def check_public(url, expected_instance=None, timeout=5):
    """Do not advertise readiness until HTTPS reaches this origin's health API."""
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=timeout) as response:
            data = json.loads(response.read(4096))
        return (isinstance(data, dict) and data.get("service") == "tractor_sim" and data.get("ok") is True
                and (expected_instance is None or data.get("instance_id") == expected_instance))
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description="双升公网启动器：本地运行，朋友通过 HTTPS 浏览器入座")
    parser.add_argument("--config", type=Path, default=ROOT / "internet.json")
    parser.add_argument("--port", type=int)
    parser.add_argument("--cloudflared", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--install-only", action="store_true")
    args = parser.parse_args()
    server = tunnel = worker = lease = None
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config.is_file() else {}
        port = validate_config(config, args.port)
        if not args.install_only:
            try:
                lease = LauncherLease()
            except BlockingIOError:
                entry_file = RUNTIME / "public-url.txt"
                if entry_file.is_file():
                    entry = entry_file.read_text(encoding="utf-8").strip()
                    print(f"公网牌桌已在运行：{entry}，继续使用已有服务。", flush=True)
                    if not args.no_browser: webbrowser.open(entry)
                else:
                    print("公网启动器已在连接中，请查看原有窗口或 runtime/launcher.stdout.log。", flush=True)
                return 0
        binary = args.cloudflared.resolve() if args.cloudflared else install_cloudflared()
        if args.install_only:
            print(f"隧道依赖已就绪：{binary}", flush=True)
            return 0
        RUNTIME.mkdir(parents=True, exist_ok=True)
        try:
            server = TractorServer(("127.0.0.1", port), max_rooms=config.get("max_rooms", 32))
        except OSError:
            # A running local teaching table can stay open. Only quick tunnels can
            # change origin ports automatically; a named tunnel has a fixed route.
            from .__main__ import existing_tractor
            if (args.port is not None or config.get("mode", "quick") != "quick"
                    or not existing_tractor(f"http://127.0.0.1:{port}", "127.0.0.1")):
                raise
            server = TractorServer(("127.0.0.1", 0), max_rooms=config.get("max_rooms", 32))
            print(f"已有本地牌桌保留在 {port}；公网牌桌使用空闲端口 {server.server_port}。", flush=True)
        (RUNTIME / "public-process.json").write_text(json.dumps({"pid": os.getpid(),
                      "started": LAUNCH_STARTED.isoformat(), "executable": sys.executable,
                      "port": server.server_port}), encoding="utf-8")
        (RUNTIME / "public-url.txt").unlink(missing_ok=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True, name="tractor-http")
        worker.start()
        local_url = f"http://127.0.0.1:{server.server_port}"
        print(f"本地牌桌已运行：{local_url}\n正在连接公网隧道，请保持这个窗口打开…", flush=True)
        tunnel = Tunnel(binary, local_url, config)
        deadline = time.monotonic() + 120
        ready = False
        progress = time.monotonic()
        while time.monotonic() < deadline:
            if tunnel.process.poll() is not None:
                raise RuntimeError("隧道进程已退出，请查看 runtime/tunnel.log")
            if tunnel.connected.is_set() and tunnel.url and check_public(tunnel.url, server.instance_id):
                ready = True; break
            if time.monotonic() - progress >= 25:
                print("仍在等待公网连通（最多等待 120 秒），详情见 runtime/tunnel.log", flush=True)
                progress = time.monotonic()
            time.sleep(.5)
        if not ready:
            raise RuntimeError("公网连接未成功：可能是网络阻止 Cloudflare 隧道或 HTTPS 验证失败。详情见 runtime/tunnel.log；未输出可用入口")
        server.public_url = tunnel.url
        (RUNTIME / "public-url.txt").write_text(tunnel.url + "\n", encoding="utf-8")
        print(f"\n公网入口已验证：{tunnel.url}\n把这个网址发给朋友；好友房间内还可复制带房间号的邀请链接。", flush=True)
        print("临时入口随隧道重启变化。Ctrl+C 停止服务和隧道。" if config.get("mode", "quick") == "quick"
              else "固定域名入口。Ctrl+C 停止服务和隧道。", flush=True)
        if not args.no_browser: webbrowser.open(tunnel.url)
        while tunnel.process.poll() is None:
            time.sleep(.5)
        raise RuntimeError("公网隧道已退出，请检查日志后重新启动")
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"启动失败：{exc}", flush=True)
        if getattr(exc, "winerror", None) in (10048, 10013):
            print("请先关闭已有的本地牌桌窗口，或运行 run-public.bat --port 8766。", flush=True)
        return 1
    finally:
        if tunnel: tunnel.close()
        if server:
            if worker: server.shutdown(); worker.join(3)
            server.server_close()
            (RUNTIME / "public-url.txt").unlink(missing_ok=True)
            (RUNTIME / "public-process.json").unlink(missing_ok=True)
        if lease: lease.close()


if __name__ == "__main__":
    raise SystemExit(main())
