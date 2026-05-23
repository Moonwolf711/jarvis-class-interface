"""JARVIS launcher — boots the Sophia/Jarvis docker container and opens Brave.

Built as a Windows .exe via PyInstaller. Tiny, single-file, no external runtime.
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


URL = "http://localhost:8181/"
BRAVE_PATHS = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
]
MAX_WAIT_SEC = 90


def info(msg: str) -> None:
    print(f"[jarvis] {msg}", flush=True)


def find_project_root() -> Path:
    """Walk up from the launcher EXE until we find docker-compose.yml."""
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
    else:
        here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents]:
        if (candidate / "docker-compose.yml").exists():
            return candidate
    # Last-ditch fallback: ~/RealtimeVoiceChat
    fallback = Path.home() / "RealtimeVoiceChat"
    if (fallback / "docker-compose.yml").exists():
        return fallback
    raise FileNotFoundError("Could not find docker-compose.yml relative to launcher or in ~/RealtimeVoiceChat")


def find_brave() -> str | None:
    for p in BRAVE_PATHS:
        if Path(p).exists():
            return p
    return None


def docker_running() -> bool:
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def compose_up(root: Path) -> None:
    info(f"running `docker compose up -d` in {root}")
    subprocess.run(["docker", "compose", "up", "-d"], cwd=str(root), check=True)


def wait_for_server(timeout_sec: int = MAX_WAIT_SEC) -> bool:
    info(f"waiting for {URL} (max {timeout_sec}s)...")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(URL, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def open_brave(url: str) -> None:
    brave = find_brave()
    if not brave:
        info("Brave not found — falling back to default browser")
        os.startfile(url)
        return
    info(f"opening {url} in Brave (app mode)")
    subprocess.Popen([
        brave,
        f"--app={url}",
        "--new-window",
        "--window-size=1400,900",
    ])


def main() -> int:
    print()
    print("     ╭───────────────────────────────╮")
    print("     │  JARVIS // PERSONA INTERFACE  │")
    print("     ╰───────────────────────────────╯")
    print()

    try:
        root = find_project_root()
    except FileNotFoundError as e:
        info(f"FATAL: {e}")
        time.sleep(5)
        return 1
    info(f"project root: {root}")

    if not docker_running():
        info("FATAL: Docker Desktop is not running. Start it and try again.")
        time.sleep(5)
        return 1
    info("docker engine OK")

    try:
        compose_up(root)
    except subprocess.CalledProcessError as e:
        info(f"FATAL: `docker compose up` failed ({e})")
        time.sleep(5)
        return 1

    if not wait_for_server():
        info("server did not respond — check `docker logs realtime-voice-chat-app`")
        time.sleep(5)
        return 1
    info("server ready")

    try:
        open_brave(URL)
    except Exception as e:
        info(f"could not open Brave: {e}; open {URL} manually")

    info("up. window opening — close this console anytime.")
    info(f"to stop the container: `docker compose down` in {root}")
    time.sleep(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
