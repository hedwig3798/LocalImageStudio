"""Starts, watches and talks to the private ComfyUI backend used by the app.

Only processes started here are ever stopped. On Windows the ComfyUI process is
placed in a Job Object with KILL_ON_JOB_CLOSE so it cannot outlive the app.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import socket
import subprocess
import time
import uuid
from collections import defaultdict
from pathlib import Path

import aiohttp
import psutil

ROOT = Path(__file__).resolve().parent.parent
COMFY_DIR = ROOT / "ComfyUI"
PY = ROOT / "venv" / "Scripts" / "python.exe"
LOG_DIR = ROOT / "logs"


def port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port(preferred: int, span: int = 20) -> int:
    for p in range(preferred, preferred + span):
        if port_free(p):
            return p
    raise RuntimeError(f"{preferred}~{preferred + span - 1} 포트가 모두 사용 중입니다.")


class _JobObject:
    """Windows Job Object: children die when the app process dies."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        k32 = ctypes.windll.kernel32
        self.handle = k32.CreateJobObjectW(None, None)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in
                        ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = EXTENDED()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info))

    def add(self, pid: int):
        if not self.handle:
            return
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1F0FFF, False, pid)
        if h:
            k32.AssignProcessToJobObject(self.handle, h)
            k32.CloseHandle(h)


class ComfyManager:
    def __init__(self, preferred_port: int = 8188, extra_flags: list[str] | None = None):
        self.preferred_port = preferred_port
        self.extra_flags = extra_flags or []
        self.port: int | None = None
        self.proc: subprocess.Popen | None = None
        self.state = "stopped"  # stopped/starting/ready/error
        self.error = ""
        self.client_id = uuid.uuid4().hex
        self.session: aiohttp.ClientSession | None = None
        self._ws_task: asyncio.Task | None = None
        self._listeners: dict[str, asyncio.Queue] = {}
        self._backlog: dict[str, list] = defaultdict(list)
        self._job = _JobObject()
        self.restarts = 0
        self.started_at = 0.0

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def pids(self) -> list[int]:
        if not self.proc:
            return []
        try:
            p = psutil.Process(self.proc.pid)
            return [p.pid] + [c.pid for c in p.children(recursive=True)]
        except psutil.Error:
            return []

    def rss_bytes(self) -> int:
        total = 0
        for pid in self.pids():
            try:
                total += psutil.Process(pid).memory_info().rss
            except psutil.Error:
                pass
        return total

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    async def start(self):
        if self.alive():
            return
        self.state = "starting"
        self.error = ""
        self.port = pick_port(self.preferred_port)
        LOG_DIR.mkdir(exist_ok=True)
        log_path = LOG_DIR / "comfyui.log"
        if log_path.exists() and log_path.stat().st_size > 20_000_000:
            log_path.replace(LOG_DIR / "comfyui.old.log")
        logf = open(log_path, "a", encoding="utf-8", errors="replace")
        logf.write(f"\n===== start {time.strftime('%Y-%m-%d %H:%M:%S')} port={self.port} flags={self.extra_flags}\n")
        logf.flush()
        # Written on every start from this folder's location, so the install can be moved/shared.
        paths_cfg = ROOT / "app" / "data" / "extra_model_paths.yaml"
        paths_cfg.parent.mkdir(parents=True, exist_ok=True)
        paths_cfg.write_text(
            "llmimagetest:\n"
            f"    base_path: {(ROOT / 'models').as_posix()}\n"
            "    checkpoints: checkpoints\n"
            "    diffusion_models: diffusion_models\n    text_encoders: text_encoders\n"
            "    vae: vae\n    loras: loras\n    model_patches: model_patches\n", encoding="utf-8")
        args = [str(PY), str(COMFY_DIR / "main.py"), "--listen", "127.0.0.1", "--port", str(self.port),
                "--disable-auto-launch", "--extra-model-paths-config", str(paths_cfg),
                "--preview-method", "none"] + self.extra_flags
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(args, cwd=str(COMFY_DIR), stdout=logf, stderr=subprocess.STDOUT,
                                     env=env, creationflags=flags)
        self._job.add(self.proc.pid)
        self.started_at = time.time()
        if self.session is None:
            self.session = aiohttp.ClientSession()
        for _ in range(240):
            if not self.alive():
                self.state = "error"
                self.error = "생성 엔진(ComfyUI)이 시작 직후 종료되었습니다. logs/comfyui.log를 확인하세요."
                return
            try:
                async with self.session.get(self.base + "/system_stats", timeout=aiohttp.ClientTimeout(total=2)) as r:
                    if r.status == 200:
                        break
            except Exception:
                pass
            await asyncio.sleep(1)
        else:
            self.state = "error"
            self.error = "생성 엔진이 4분 안에 준비되지 않았습니다."
            return
        self._ws_task = asyncio.create_task(self._ws_loop())
        self.state = "ready"

    async def stop(self):
        if self._ws_task:
            self._ws_task.cancel()
            self._ws_task = None
        if self.proc and self.proc.poll() is None:
            for pid in reversed(self.pids()):
                try:
                    psutil.Process(pid).terminate()
                except psutil.Error:
                    pass
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.proc.wait, 20)
            except subprocess.TimeoutExpired:
                for pid in self.pids():
                    try:
                        psutil.Process(pid).kill()
                    except psutil.Error:
                        pass
        self.proc = None
        self.state = "stopped"

    async def restart(self):
        await self.stop()
        self.restarts += 1
        await self.start()

    async def close(self):
        await self.stop()
        if self.session:
            await self.session.close()

    # ------------------------------------------------------------ websocket
    async def _ws_loop(self):
        url = f"ws://127.0.0.1:{self.port}/ws?clientId={self.client_id}"
        while True:
            try:
                async with self.session.ws_connect(url, heartbeat=30, max_msg_size=0) as ws:
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data = json.loads(msg.data)
                        pid = (data.get("data") or {}).get("prompt_id")
                        if not pid:
                            continue
                        item = (time.time(), data["type"], data["data"])
                        q = self._listeners.get(pid)
                        if q:
                            q.put_nowait(item)
                        else:
                            self._backlog[pid].append(item)
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(1)
                if not self.alive():
                    return

    def listen(self, prompt_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        for item in self._backlog.pop(prompt_id, []):
            q.put_nowait(item)
        self._listeners[prompt_id] = q
        return q

    def unlisten(self, prompt_id: str):
        self._listeners.pop(prompt_id, None)

    # ------------------------------------------------------------ http
    async def post(self, path: str, payload: dict | None = None) -> tuple[int, dict | str]:
        async with self.session.post(self.base + path, json=payload or {}) as r:
            txt = await r.text()
            try:
                return r.status, json.loads(txt)
            except json.JSONDecodeError:
                return r.status, txt

    async def get_json(self, path: str):
        async with self.session.get(self.base + path) as r:
            return await r.json()

    async def get_bytes(self, path: str, params: dict) -> bytes:
        async with self.session.get(self.base + path, params=params) as r:
            r.raise_for_status()
            return await r.read()

    async def submit(self, graph: dict) -> dict:
        status, body = await self.post("/prompt", {"prompt": graph, "client_id": self.client_id})
        if status != 200 or not isinstance(body, dict) or "prompt_id" not in body:
            raise SubmitError(body)
        return body

    async def interrupt(self, prompt_id: str | None = None):
        await self.post("/interrupt", {"prompt_id": prompt_id} if prompt_id else {})

    async def free(self, unload_models: bool = True):
        await self.post("/free", {"unload_models": unload_models, "free_memory": True})


class SubmitError(Exception):
    def __init__(self, body):
        super().__init__(json.dumps(body, ensure_ascii=False)[:3000] if not isinstance(body, str) else body[:3000])
        self.body = body
