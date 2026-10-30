"""Entry point used by the desktop BAT.

  launcher.py            start the app (or open the running one)
  launcher.py --stop     stop only the processes this app started
  launcher.py --repair   --stop + clear stale state/temp files

Single instance: an OS file lock (data/instance.lock) + instance.json with PIDs/ports.
"""
from __future__ import annotations

import asyncio
import json
import msvcrt
import os
import shutil
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
LOCK = DATA / "instance.lock"
INSTANCE = DATA / "instance.json"
sys.path.insert(0, str(HERE))


def say(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_instance() -> dict:
    try:
        return json.loads(INSTANCE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def ping(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
            return json.loads(r.read()).get("app") == "LLMImageTest-KoreanGUI"
    except Exception:
        return False


def ours(pid: int) -> bool:
    """True only for processes started from this install (venv python under ROOT)."""
    import psutil
    try:
        p = psutil.Process(pid)
        exe = (p.exe() or "").lower()
        cmd = " ".join(p.cmdline()).lower()
        return str(ROOT).lower() in exe or str(ROOT).lower() in cmd
    except psutil.Error:
        return False


def stop_running(repair=False) -> int:
    import psutil
    inst = read_instance()
    killed = 0
    pids = [inst.get("app_pid")] + inst.get("comfy_pids", [])
    if inst.get("app_port") and ping(inst["app_port"]):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{inst['app_port']}/api/shutdown", data=b"{}",
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
            say("실행 중인 앱에 종료를 요청했습니다.")
            time.sleep(3)
        except Exception:
            pass
    for pid in [p for p in pids if p]:
        if psutil.pid_exists(pid) and ours(pid):
            try:
                proc = psutil.Process(pid)
                for c in proc.children(recursive=True):
                    if ours(c.pid):
                        c.kill()
                        killed += 1
                proc.kill()
                killed += 1
            except psutil.Error:
                pass
    if repair:
        # Also catch orphans of THIS install only (ComfyUI main.py from our folder)
        for p in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(p.info.get("cmdline") or [])
            if str(ROOT / "ComfyUI" / "main.py").lower() in cmd.lower() or str(HERE / "launcher.py").lower() in cmd.lower():
                if p.pid != os.getpid() and p.pid not in (os.getppid(),):
                    try:
                        p.kill()
                        killed += 1
                    except psutil.Error:
                        pass
        for f in (INSTANCE,):
            f.unlink(missing_ok=True)
        tmp = ROOT / "ComfyUI" / "temp"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    say(f"정리한 프로세스: {killed}개 (이 앱이 시작한 프로세스만 대상)")
    if repair:
        say("완료되었습니다. 다시 사용하려면 바탕화면의 '로컬 이미지 생성기'를 실행하세요.")
    return killed


def main():
    DATA.mkdir(exist_ok=True)
    if "--stop" in sys.argv or "--repair" in sys.argv:
        stop_running(repair="--repair" in sys.argv)
        return 0

    # ---- single instance
    lock_fh = open(LOCK, "a+")
    try:
        msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        say("이미 실행 중입니다. 기존 창을 엽니다.")
        for _ in range(240):
            inst = read_instance()
            if inst.get("ready") and inst.get("app_port") and ping(inst["app_port"]):
                webbrowser.open(f"http://127.0.0.1:{inst['app_port']}/")
                return 0
            time.sleep(1)
        say("기존 실행이 응답하지 않습니다. '이미지생성기_복구.bat'을 실행해 주세요.")
        return 1

    from comfy_manager import pick_port
    import server

    settings = server.load_settings()
    port = pick_port(7860)
    if port != 7860:
        say(f"7860 포트가 다른 프로그램에서 사용 중이라 {port} 포트를 사용합니다.")
    say(f"앱 서버 시작: http://127.0.0.1:{port}/  (이 PC에서만 접속 가능)")
    say("생성 엔진(ComfyUI)을 시작하는 중입니다. 준비되면 브라우저가 자동으로 열립니다...")
    INSTANCE.write_text(json.dumps({"app_pid": os.getpid(), "app_port": port, "ready": False,
                                    "started": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")

    def on_ready(state):
        INSTANCE.write_text(json.dumps({"app_pid": os.getpid(), "app_port": port, "ready": True,
                                        "comfy_port": state.comfy.port, "comfy_pids": state.comfy.pids(),
                                        "started": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
        if state.comfy.state == "ready":
            say(f"생성 엔진 준비 완료 (내부 포트 {state.comfy.port}).")
        else:
            say(f"생성 엔진 시작 실패: {state.comfy.error}")
        if settings.get("open_browser", True) and "--no-browser" not in sys.argv:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        say("이 창을 닫으면 앱과 생성 엔진이 함께 종료됩니다.")

    try:
        asyncio.run(server.serve(port, on_ready))
    except KeyboardInterrupt:
        pass
    finally:
        INSTANCE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
