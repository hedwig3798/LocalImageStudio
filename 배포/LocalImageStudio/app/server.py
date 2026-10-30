"""Local Korean GUI server for Anima / Krea 2 / Qwen-Image 2.1 (localhost only).

Runs a private ComfyUI backend (see comfy_manager.py), keeps its own job queue,
converts GUI settings to official workflows (workflows.py), and stores results +
history. Nothing is sent to cloud services; prompts are passed through unchanged
unless the user turns on the optional local translation/enhancement.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import random
import re
import shutil
import sys
import time
import traceback
import uuid
from pathlib import Path

import psutil
from aiohttp import web
from PIL import Image, ImageOps, ImageStat, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_scan  # noqa: E402
import tag_search  # noqa: E402
import models_config as MC  # noqa: E402
import workflows as WF  # noqa: E402
from comfy_manager import ROOT, COMFY_DIR, ComfyManager, SubmitError  # noqa: E402

APP_DIR = ROOT / "app"
DATA = APP_DIR / "data"
STATIC = APP_DIR / "static"
INPUT_DIR = COMFY_DIR / "input"
HISTORY = DATA / "history.jsonl"
SETTINGS = DATA / "settings.json"
EVENTS = ROOT / "logs" / "app_events.jsonl"
APP_SIGNATURE = "LLMImageTest-KoreanGUI"
APP_VERSION = "1.3.0"

DEFAULT_SETTINGS = {
    "output_dir": str(ROOT / "outputs"),
    "comfy_port": 8188,
    # Measured on this PC (docs/테스트_결과.md): pinned memory made Krea 2 use 18GB RAM and
    # dropped free RAM to 2GB; without it RAM stays ~2.3GB at similar speed. Keep DynamicVRAM on.
    "comfy_flags": ["--disable-pinned-memory"],
    "open_browser": True,
}

TRANSLATE_INSTRUCTION = (
    "Translate the following image-generation prompt into natural English. Keep every visual detail, "
    "names, numbers, quoted text and tags exactly. Do not add, remove, soften or censor anything. "
    "Output only the translated prompt.")
ENHANCE_INSTRUCTION = (
    "Rewrite the following image-generation prompt into one detailed English paragraph that describes the "
    "finished image (subject, composition, lighting, style, colors). Keep all of the user's content and intent; "
    "do not remove, soften or censor anything and do not add unrelated subjects. Output only the prompt.")


def log_event(kind: str, **kw):
    EVENTS.parent.mkdir(exist_ok=True)
    rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": kind, **kw}
    with open(EVENTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_settings() -> dict:
    DATA.mkdir(exist_ok=True)
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS.exists():
        try:
            s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
        except Exception:
            pass
    return s


def save_settings(s: dict):
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- error messages

def korean_error(raw: str) -> str:
    r = raw.lower()
    if "out of memory" in r or "outofmemory" in r or "allocation on device" in r or "cudaerrormemoryallocation" in r:
        return ("GPU 메모리(VRAM)가 부족합니다. 해상도를 낮추거나 '저메모리' 프리셋을 선택하고, 게임 등 GPU를 쓰는 "
                "프로그램을 종료한 뒤 다시 시도하세요. (앱 하단의 '모델 메모리 해제'도 도움이 됩니다)")
    if "paging file" in r or "1455" in r or "memoryerror" in r or "not enough memory" in r or "cannot allocate memory" in r:
        return ("시스템 메모리(RAM/가상 메모리)가 부족합니다. 다른 프로그램을 닫거나 '모델 메모리 해제' 후 "
                "다시 시도하세요. 반복되면 Windows 가상 메모리(페이지 파일)를 늘려야 할 수 있습니다.")
    if "value_not_in_list" in r or "not in list" in r or "no such file" in r:
        return "필요한 모델/이미지 파일을 찾지 못했습니다. 설치가 끝났는지, 참조 이미지를 다시 올렸는지 확인하세요."
    if "interrupted" in r or "취소" in r:
        return "사용자가 작업을 취소했습니다."
    if "cannot connect" in r or "connection refused" in r or "server disconnected" in r or "엔진" in raw:
        return "생성 엔진(ComfyUI)과 연결이 끊겼습니다. 앱이 엔진을 다시 시작합니다. 잠시 후 다시 시도하세요."
    if "nan" in r and "tensor" in r:
        return "계산 중 수치 오류(NaN)가 발생했습니다. 다른 시드나 설정으로 다시 시도하세요."
    return "생성 중 오류가 발생했습니다: " + raw[:300]


# ---------------------------------------------------------------- param validation

APP_LORAS = {"krea2_style_reference.safetensors", "qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors",
             "krea2_turbo_lora_rank_64_bf16.safetensors"}


def families() -> dict:
    """Static model families + SD checkpoints/LoRAs found in models/ (re-scanned on each call, header-only)."""
    fams, _ = model_scan.sd_families(MC.FAMILIES, ROOT / "models", APP_LORAS)
    return fams


def snap(v: int, mult: int, lo: int, hi: int) -> int:
    v = int(round(int(v) / mult) * mult)
    return max(lo - lo % mult, min(hi, v))


def normalize(params: dict) -> dict:
    """Server-side validation; never trust GUI values blindly."""
    fams = families()
    fam_id = params.get("family")
    if fam_id not in fams:
        raise ValueError("알 수 없는 모델입니다.")
    fam = fams[fam_id]
    mode = params.get("mode", "t2i")
    minfo = fam["modes"].get(mode)
    if not minfo or not minfo["supported"]:
        raise ValueError(f"{fam['label']}은(는) '{MC.MODES.get(mode, mode)}'를 지원하지 않습니다. "
                         + (minfo or {}).get("reason", ""))
    variant = params.get("variant") or fam["presets"]["default"].get("variant", "")
    if variant not in fam["variants"]:
        if fam.get("dynamic_variants") and not fam["variants"]:
            raise ValueError("models\\checkpoints 폴더에 사용할 수 있는 SDXL/SD1.5 체크포인트가 없습니다.")
        raise ValueError("이 모델에 없는 변형입니다.")
    vinfo = fam["variants"][variant]
    p = {"family": fam_id, "mode": mode, "variant": variant,
         "variant_label": vinfo["label"], "precision": vinfo["precision"], "family_label": fam["label"]}
    if fam_id in ("sdxl", "sd35"):
        p["ckpt"], p["arch"] = vinfo["file"], vinfo["arch"]
        if not (ROOT / "models" / "checkpoints" / p["ckpt"]).exists():
            raise ValueError(f"체크포인트 파일이 없습니다: {p['ckpt']} ('설치.bat'으로 받거나 models\\checkpoints에 넣으세요)")
    p["prompt"] = str(params.get("prompt", ""))
    if not p["prompt"].strip():
        raise ValueError("프롬프트를 입력하세요.")
    p["negative"] = str(params.get("negative", "")) if fam["variants"][variant]["negative"] else ""
    m = fam["multiple"]
    p["req_width"], p["req_height"] = int(params.get("width", 1024)), int(params.get("height", 1024))
    p["width"] = snap(p["req_width"], m, fam["min_side"], fam["max_side"])  # inpaint overrides below
    p["height"] = snap(p["req_height"], m, fam["min_side"], fam["max_side"])
    p["steps"] = max(1, min(100, int(params.get("steps", 8))))
    p["cfg"] = max(0.0, min(20.0, float(params.get("cfg", 1.0))))
    p["sampler"] = params.get("sampler") if params.get("sampler") in MC.COMMON_SAMPLERS else "euler"
    p["scheduler"] = params.get("scheduler") if params.get("scheduler") in MC.COMMON_SCHEDULERS else "simple"
    seed = params.get("seed")
    p["random_seed"] = bool(params.get("random_seed", True))
    p["seed"] = random.randint(0, 2**48) if p["random_seed"] or seed in (None, "") else max(0, int(seed))
    imgs = [str(x) for x in params.get("images", []) if x]
    if mode != "t2i":
        if not imgs:
            raise ValueError("이 모드에는 참조 이미지가 필요합니다. 이미지를 올려 주세요.")
        imgs = imgs[: minfo.get("max_images", 1)]
        for name in imgs:
            if not re.fullmatch(r"llmapp_[0-9a-f]{32}\.png", name) or not (INPUT_DIR / name).exists():
                raise ValueError("참조 이미지를 찾을 수 없습니다. 다시 올려 주세요.")
    p["images"] = imgs
    if mode == "inpaint":
        mask = str(params.get("mask") or "")
        if not re.fullmatch(r"llmapp_[0-9a-f]{32}\.png", mask) or not (INPUT_DIR / mask).exists():
            raise ValueError("고칠 영역을 이미지 위에 칠해 주세요(마스크가 없습니다).")
        p["mask"] = mask
        # generation size follows the input image (about 1MP, SD1.5 about 0.4MP), snapped to the model multiple
        with Image.open(INPUT_DIR / imgs[0]) as im:
            w0, h0 = im.size
        # keep the input's own size when it is already in a sensible range, otherwise scale into it
        lo, hi = (0.25e6, 0.45e6) if p.get("arch") == "sd15" else (0.6e6, 1.1e6)
        area = w0 * h0
        k = (min(max(area, lo), hi) / area) ** 0.5
        p["width"] = snap(w0 * k, m, fam["min_side"], fam["max_side"])
        p["height"] = snap(h0 * k, m, fam["min_side"], fam["max_side"])
        p["mask_grow"] = max(0, min(64, int(params.get("mask_grow", 8))))
    loras = []
    # per-family declaration in models_config ("model_clip" | "model_only"); workflows.user_loras() applies it
    p["lora_apply"] = fam.get("lora") if fam.get("lora") in MC.LORA_APPLY else ("model_clip" if fam.get("lora") else "")
    if fam.get("lora"):
        allowed = {l["name"]: l for l in fam.get("loras", [])}
        for lo in params.get("loras", [])[:3]:
            name = str(lo.get("name", ""))
            if not name:
                continue
            if name not in allowed:
                raise ValueError(f"LoRA 파일을 찾을 수 없거나 이 모델({fam['label']})과 맞지 않는 LoRA입니다: {name}"
                                 " (사용 가능한 목록: imagegen.bat models)")
            if not model_scan.lora_fits_variant(allowed[name], vinfo):
                lab = allowed[name].get("arch_label", allowed[name]["arch"])
                raise ValueError(f"이 LoRA는 {lab}용이라 선택한 변형({vinfo['label']})에서는 쓸 수 없습니다: {name}"
                                 f" (텐서 크기가 달라 적용되지 않습니다. {lab} 모델(변형)을 고르거나 다른 LoRA를 쓰세요)")
            loras.append({"name": name, "strength": max(-2.0, min(2.0, float(lo.get("strength", 1.0))))})
    p["loras"] = loras
    p["strength"] = max(0.05, min(1.0, float(params.get("strength", fam.get("img2img_default_strength", 0.6)))))
    p["ref_strength"] = max(0.0, min(2.0, float(params.get("ref_strength", 1.0))))
    p["edit_custom_size"] = mode == "reference" or bool(params.get("edit_custom_size", False))
    p["ref_resolution"] = 1024
    if fam_id == "qwen21":
        p["cache_device"] = params.get("cache_device") if params.get("cache_device") in ("auto", "gpu", "cpu", "off") else "auto"
    tv = params.get("tiled_vae", "auto")
    p["tiled_vae"] = (fam_id != "anima") if tv == "auto" else bool(tv)
    p["translate"] = bool(params.get("translate", False))
    p["enhance"] = bool(params.get("enhance", False))
    p["quality_prefix"] = bool(params.get("quality_prefix", fam_id == "anima"))
    p["rating_tag"] = params.get("rating_tag", "") if params.get("rating_tag", "") in fam.get("rating_tags", [""]) else ""
    exp = params.get("export", "none")
    p["export"] = exp if exp in MC.EXPORT_SIZES else "none"
    p["preset"] = params.get("preset", "")
    return p


# ---------------------------------------------------------------- app state

class AppState:
    def __init__(self):
        self.settings = load_settings()
        self.comfy = ComfyManager(self.settings["comfy_port"], self.settings["comfy_flags"])
        self.queue: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.current: dict | None = None
        self.loaded_family: str | None = None
        self.loaded_variant: str | None = None
        self.wakeup = asyncio.Event()
        self.mem = {"vram_used": 0, "vram_total": 0, "ram_used": 0, "ram_total": 0, "ram_avail": 0,
                    "comfy_rss": 0, "gpu_util": 0}
        self.peak = None
        self.app_port = None
        self.shutting_down = False

    # ------------------------------------------------------------ monitor
    async def monitor_loop(self):
        while True:
            try:
                p = await asyncio.create_subprocess_exec(
                    "nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits", stdout=asyncio.subprocess.PIPE,
                    creationflags=0x08000000 if os.name == "nt" else 0)
                outb, _ = await p.communicate()
                used, total, util = [int(x.strip()) for x in outb.decode().splitlines()[0].split(",")]
                self.mem.update(vram_used=used, vram_total=total, gpu_util=util)
            except Exception:
                pass
            vm = psutil.virtual_memory()
            self.mem.update(ram_used=vm.total - vm.available, ram_total=vm.total, ram_avail=vm.available,
                            comfy_rss=self.comfy.rss_bytes())
            if self.peak is not None:
                self.peak["vram"] = max(self.peak["vram"], self.mem["vram_used"])
                self.peak["rss"] = max(self.peak["rss"], self.mem["comfy_rss"])
                self.peak["min_avail"] = min(self.peak["min_avail"], vm.available)
            if self.comfy.state == "ready" and not self.comfy.alive():
                self.comfy.state = "error"
                self.comfy.error = "생성 엔진이 예기치 않게 종료되었습니다(메모리 부족 가능). 다음 작업 때 자동으로 다시 시작합니다."
                self.loaded_family = None
                log_event("comfy_crashed")
            await asyncio.sleep(1.5)

    # ------------------------------------------------------------ queue
    def enqueue(self, params: dict, count: int = 1) -> list[dict]:
        out = []
        for i in range(max(1, min(8, count))):
            p = normalize(params)
            if i > 0 and not p["random_seed"]:
                p["seed"] = p["seed"] + i  # same settings, next seed
            out.append(self._add_job(p))
        return out

    def _add_job(self, p: dict) -> dict:
        job = {"id": uuid.uuid4().hex[:12], "params": p, "status": "queued", "phase": "대기 중",
               "progress": 0, "progress_max": 0, "created": time.time(), "result": None, "error": None}
        self.jobs[job["id"]] = job
        self.queue.append(job)
        self.wakeup.set()
        return job

    async def cancel(self, job_id: str) -> str:
        job = self.jobs.get(job_id)
        if not job:
            return "없는 작업입니다."
        if job["status"] == "queued":
            job["status"] = "cancelled"
            job["phase"] = "취소됨"
            self.queue = [j for j in self.queue if j["id"] != job_id]
            log_event("cancel_queued", job=job_id)
            return "대기 중인 작업을 취소했습니다."
        if job["status"] == "running":
            job["cancel_requested"] = True
            job["phase"] = "취소 요청 중..."
            try:
                await self.comfy.interrupt(job.get("prompt_id"))
            except Exception:
                pass
            log_event("cancel_running", job=job_id)
            return "실행 중인 작업에 취소를 요청했습니다."
        return "이미 끝난 작업입니다."

    async def worker(self):
        while not self.shutting_down:
            if not self.queue:
                self.wakeup.clear()
                await self.wakeup.wait()
                continue
            job = self.queue.pop(0)
            if job["status"] != "queued":
                continue
            self.current = job
            try:
                await self.run_job(job)
            except Exception as e:  # never let the worker die
                job["status"] = "error"
                job["error_raw"] = traceback.format_exc()[-3000:]
                job["error"] = korean_error(str(e))
                log_event("job_exception", job=job["id"], error=job["error_raw"])
            finally:
                job["finished"] = time.time()
                self.current = None

    # ------------------------------------------------------------ job execution
    async def ensure_engine(self, job):
        if not self.comfy.alive():
            job["phase"] = "생성 엔진 시작 중..."
            self.loaded_family = None
            await self.comfy.restart() if self.comfy.proc else await self.comfy.start()
            log_event("comfy_started", port=self.comfy.port, state=self.comfy.state)
        if self.comfy.state != "ready":
            raise RuntimeError(self.comfy.error or "생성 엔진이 준비되지 않았습니다.")

    async def run_graph(self, job, graph, meta, label):
        """Submit one graph and follow it. Returns (history_entry, timing)."""
        engine_gen = self.comfy.started_at  # changes when the engine is restarted
        sub = await self.comfy.submit(graph)
        pid = sub["prompt_id"]
        job["prompt_id"] = pid
        q = self.comfy.listen(pid)
        t = {"submitted": time.time(), "node_start": {}, "progress_times": []}
        ws_outputs: dict = {}
        phases = meta["phases"]
        try:
            while True:
                try:
                    ts, typ, data = await asyncio.wait_for(q.get(), timeout=5)
                except asyncio.TimeoutError:
                    if not self.comfy.alive() or self.comfy.started_at != engine_gen:
                        if job.get("cancel_requested"):
                            raise asyncio.CancelledError("engine restarted after cancel")
                        raise RuntimeError("생성 엔진(ComfyUI)이 종료되었거나 재시작되어 이 작업을 끝내지 못했습니다.")
                    continue
                if typ == "executing":
                    node = data.get("node")
                    if node is None:
                        break
                    t["node_start"].setdefault(node, ts)
                    job["phase"] = f"{label}: {phases.get(node, '처리 중')}"
                elif typ == "progress":
                    job["progress"], job["progress_max"] = data.get("value", 0), data.get("max", 0)
                    t["progress_times"].append((ts, data.get("node")))
                    if phases.get(data.get("node")) == WF.PHASE_SAMPLE:
                        job["phase"] = f"{label}: {WF.PHASE_SAMPLE} {job['progress']}/{job['progress_max']}"
                elif typ == "executed":
                    if data.get("node") and data.get("output") is not None:
                        ws_outputs[data["node"]] = data["output"]
                elif typ == "execution_cached":
                    for n in data.get("nodes", []):
                        t["node_start"].setdefault(n, ts)
                elif typ == "execution_error":
                    raise RuntimeError(f"{data.get('node_type')}: {data.get('exception_message')}")
                elif typ == "execution_interrupted":
                    raise asyncio.CancelledError("interrupted")
                elif typ == "execution_success":
                    break
        finally:
            self.comfy.unlisten(pid)
        t["finished"] = time.time()
        # Outputs come from the websocket "executed" messages. /history is only written
        # after the success message, so poll it briefly for cached nodes (race fixed 2026-10-15).
        hist = {}
        out_node = meta["output_node"]
        for _ in range(20):
            hist = (await self.comfy.get_json(f"/history/{pid}")).get(pid, {})
            if out_node in ws_outputs or out_node in hist.get("outputs", {}):
                break
            await asyncio.sleep(0.25)
        hist.setdefault("outputs", {}).update(ws_outputs)
        return hist, t

    def _timing_summary(self, t, phases, meta_sampler=None):
        starts = t["node_start"]
        decode_nodes = [n for n, ph in phases.items() if ph == WF.PHASE_DECODE and n in starts]
        s_start = starts.get(meta_sampler) if meta_sampler else None
        d_start = min((starts[n] for n in decode_nodes), default=None)
        out = {"total_s": round(t["finished"] - t["submitted"], 2)}
        if s_start:
            out["prepare_s"] = round(s_start - t["submitted"], 2)   # model file load + text/image encode (incl. TE to GPU)
        if s_start and d_start:
            out["sampling_s"] = round(d_start - s_start, 2)          # includes moving weights to GPU at step 1
            out["decode_save_s"] = round(t["finished"] - d_start, 2)
        pt = [ts for ts, n in t["progress_times"] if n == meta_sampler]
        if len(pt) >= 2 and s_start:
            out["first_step_s"] = round(pt[0] - s_start, 2)
            out["avg_step_s"] = round((pt[-1] - pt[0]) / (len(pt) - 1), 3)
        return out

    async def text_generate(self, job, instruction, text) -> str:
        graph, meta = WF.build_textgen(instruction, text)
        hist, _ = await self.run_graph(job, graph, meta, "프롬프트 처리")
        outp = hist.get("outputs", {}).get(meta["output_node"], {})
        val = outp.get("text") or outp.get("string") or []
        if isinstance(val, list):
            val = val[0] if val else ""
        res = str(val).strip()
        res = re.sub(r"<think>.*?</think>", "", res, flags=re.S).strip().strip('"')
        if not res:
            raise RuntimeError("로컬 LLM이 빈 결과를 반환했습니다.")
        return res

    async def run_job(self, job):
        p = job["params"]
        job["status"] = "running"
        job["started"] = time.time()
        self.peak = {"vram": 0, "rss": 0, "min_avail": psutil.virtual_memory().available}
        await self.ensure_engine(job)
        timings = {}
        switch_note = None
        try:
            # 1) free the previous model family so only one stays loaded
            # Also free on a variant change inside one family (e.g. Qwen Turbo -> Base): keeping both
            # DiTs pushed VRAM past 8GB and the driver fell back to shared memory (measured 448s run).
            if self.loaded_family and (self.loaded_family != p["family"] or self.loaded_variant != p["variant"]):
                job["phase"] = f"이전 모델({MC.FAMILIES.get(self.loaded_family, {}).get('label', self.loaded_family)}) 메모리 해제 중"
                await self.comfy.free(True)
                switch_note = f"{self.loaded_variant or self.loaded_family} → {p['variant']} 전환: 이전 모델 해제"
                log_event("model_switch", frm=self.loaded_variant, to=p["variant"])
                self.loaded_family = self.loaded_variant = None
                await asyncio.sleep(1.0)

            # 2) prompt processing (optional, local); original prompt is always kept
            final = p["prompt"]
            steps_log = []
            if p["translate"] or p["enhance"]:
                t0 = time.time()
                if p["translate"]:
                    final = await self.text_generate(job, TRANSLATE_INSTRUCTION, final)
                    steps_log.append("번역")
                if p["enhance"]:
                    final = await self.text_generate(job, ENHANCE_INSTRUCTION, final)
                    steps_log.append("보강")
                timings["prompt_llm_s"] = round(time.time() - t0, 2)
                if p["family"] != "krea2":
                    await self.comfy.free(True)  # drop the 4B LLM before loading another family
            if p["family"] == "anima":
                prefix = MC.FAMILIES["anima"]["quality_prefix"][p["variant"]] if p["quality_prefix"] else ""
                if p["rating_tag"]:
                    prefix += p["rating_tag"] + ", "
                final = prefix + final
            p["final_prompt"] = final
            p["final_negative"] = p["negative"] if p["cfg"] > 1.0 else ""
            if p["family"] == "anima" and p["cfg"] > 1.0 and p["rating_tag"]:
                have = {t.strip().lower() for t in p["final_negative"].split(",")}
                add = [t.strip() for t in MC.FAMILIES["anima"]["rating_negative"].get(p["rating_tag"], "").split(",")
                       if t.strip() and t.strip() not in have]
                if add:
                    p["final_negative"] = ", ".join(x for x in [p["final_negative"].strip().rstrip(","), *add] if x)
            p["prompt_processing"] = steps_log

            # 3) generate
            graph, meta = WF.BUILDERS[p["family"]](p)
            hist, t = await self.run_graph(job, graph, meta, "생성")
            self.loaded_family, self.loaded_variant = p["family"], p["variant"]
            timings.update(self._timing_summary(t, meta["phases"], meta.get("sampler_node")))
            data = await self._fetch_output(hist, meta)
            job["phase"] = "저장 중"
            record = self.save_result(job, p, data, timings, switch_note)
            job["result"] = record
            job["status"] = "done"
            job["phase"] = "완료"
            log_event("job_done", job=job["id"], family=p["family"], mode=p["mode"], timings=timings)
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            job["phase"] = "취소됨"
            job["error"] = "사용자가 작업을 취소했습니다."
            log_event("job_cancelled", job=job["id"])
        except SubmitError as e:
            job["status"] = "error"
            job["error_raw"] = str(e)
            job["error"] = korean_error(str(e))
            log_event("job_submit_error", job=job["id"], error=str(e)[:2000])
        except Exception as e:
            job["status"] = "error"
            job["error_raw"] = f"{e}\n{traceback.format_exc()[-2000:]}"
            job["error"] = korean_error(str(e))
            log_event("job_error", job=job["id"], error=job["error_raw"][:3000])
            if not self.comfy.alive():
                self.loaded_family = None
        finally:
            job["peak"] = {"vram_mib": self.peak["vram"], "comfy_rss_gb": round(self.peak["rss"] / 2**30, 2),
                           "min_sys_avail_gb": round(self.peak["min_avail"] / 2**30, 2)}
            self.peak = None

    async def _fetch_output(self, hist, meta) -> bytes:
        imgs = hist.get("outputs", {}).get(meta["output_node"], {}).get("images", [])
        if not imgs:
            raise RuntimeError("결과 이미지가 반환되지 않았습니다.")
        im = imgs[0]
        return await self.comfy.get_bytes("/view", {"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                                    "type": im.get("type", "temp")})

    # ------------------------------------------------------------ results
    def save_result(self, job, p, data: bytes, timings, switch_note) -> dict:
        out_dir = Path(self.settings["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        img = Image.open(io.BytesIO(data))
        img.load()
        gen_size = img.size
        stat = ImageStat.Stat(img.convert("RGB"))
        quality = {"mean": [round(x, 1) for x in stat.mean], "std": [round(x, 1) for x in stat.stddev]}
        quality["suspicious"] = max(stat.stddev) < 3.0
        final_img = img
        exp = MC.EXPORT_SIZES[p["export"]]
        if exp[1] and (exp[1], exp[2]) != gen_size:
            final_img = ImageOps.fit(img, (exp[1], exp[2]), Image.LANCZOS, centering=(0.5, 0.5))
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = f"{stamp}_{p['family']}_{p['mode']}_{p['seed']}"
        rec = {
            "id": uuid.uuid4().hex[:12], "job_id": job["id"], "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "family": p["family"], "family_label": p["family_label"],
            "variant": p["variant"], "variant_label": p["variant_label"],
            "precision": p["precision"],
            "files": ({**WF.FILES[p["variant"]], "loras": p["loras"]} if p["variant"] in WF.FILES and p.get("loras")
                      else WF.FILES.get(p["variant"]) or {"checkpoint": p.get("ckpt"), "loras": p.get("loras", [])}),
            "loras": p.get("loras", []),
            "mode": p["mode"], "mode_label": MC.MODES[p["mode"]],
            "prompt": p["prompt"], "final_prompt": p["final_prompt"], "negative": p["negative"],
            "final_negative": p["final_negative"], "prompt_processing": p.get("prompt_processing", []),
            "requested_size": [p["req_width"], p["req_height"]], "gen_size": list(gen_size),
            "final_size": list(final_img.size),
            "steps": p["steps"], "cfg": p["cfg"], "sampler": p["sampler"], "scheduler": p["scheduler"],
            "seed": p["seed"], "strength": p["strength"] if p["mode"] in ("img2img", "inpaint") else None,
            "ref_strength": p["ref_strength"] if (p["mode"] == "reference" and p["family"] == "krea2") else None,
            "input_images": p["images"], "timings": timings,
            "quality_check": quality, "model_switch": switch_note, "params": p,
        }
        rec["peak"] = {"vram_mib": self.peak["vram"], "comfy_rss_gb": round(self.peak["rss"] / 2**30, 2),
                       "min_sys_avail_gb": round(self.peak["min_avail"] / 2**30, 2)}
        meta = PngImagePlugin.PngInfo()
        meta.add_text("llmimagetest", json.dumps({k: v for k, v in rec.items() if k != "params"}, ensure_ascii=False))
        final_path = out_dir / f"{base}.png"
        final_img.save(final_path, pnginfo=meta)
        rec["path"] = str(final_path)
        if final_img is not img:
            gdir = out_dir / "원본_생성크기"
            gdir.mkdir(exist_ok=True)
            gpath = gdir / f"{base}_gen{gen_size[0]}x{gen_size[1]}.png"
            img.save(gpath, pnginfo=meta)
            rec["gen_path"] = str(gpath)
        # keep copies of the input images next to the history for reproducibility
        if p["images"]:
            idir = out_dir / "입력_이미지"
            idir.mkdir(exist_ok=True)
            rec["input_copies"] = []
            for name in p["images"] + ([p["mask"]] if p.get("mask") else []):
                dst = idir / name
                if not dst.exists():
                    shutil.copy2(INPUT_DIR / name, dst)
                rec["input_copies"].append(str(dst))
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec


def read_history(limit=200) -> list[dict]:
    if not HISTORY.exists():
        return []
    rows = []
    for ln in HISTORY.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
    return rows[-limit:][::-1]


def read_history_page(offset: int, limit: int) -> dict:
    """One page of history, newest first, plus the total count (for the UI pager)."""
    rows = read_history(10**9)
    total = len(rows)
    offset = max(0, offset)
    limit = max(1, min(limit, 500))
    return {"items": rows[offset:offset + limit], "total": total, "offset": offset, "limit": limit}


def recycle(paths: list[str]) -> int:
    """Move files to the Windows Recycle Bin (recoverable) instead of deleting them."""
    paths = [p for p in paths if p and Path(p).is_file()]
    if not paths or os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    # FO_DELETE=3; FOF_SILENT|FOF_NOCONFIRMATION|FOF_ALLOWUNDO|FOF_NOERRORUI
    op = SHFILEOPSTRUCTW(None, 3, "\0".join(str(Path(p).resolve()) for p in paths) + "\0\0", None,
                         0x0004 | 0x0010 | 0x0040 | 0x0400, False, None, None)
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0:
        raise OSError("휴지통으로 이동하지 못했습니다.")
    return len(paths)


def delete_history(ids: set[str] | None, delete_files: bool) -> dict:
    """Remove records (ids=None → all). Files go to the Recycle Bin only when asked;
    an input copy is kept while another remaining record still uses it."""
    rows = read_history(10**9)[::-1]  # file order
    gone = [r for r in rows if ids is None or r["id"] in ids]
    keep = [r for r in rows if not (ids is None or r["id"] in ids)]
    files = 0
    if delete_files and gone:
        still_used = {p for r in keep for p in r.get("input_copies", [])}
        targets = [p for r in gone for p in (r.get("path"), r.get("gen_path"))]
        targets += [p for r in gone for p in r.get("input_copies", []) if p not in still_used]
        files = recycle(list(dict.fromkeys(targets)))
    tmp = HISTORY.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
    os.replace(tmp, HISTORY)
    return {"deleted": len(gone), "files_recycled": files, "remaining": len(keep),
            "ids": [r["id"] for r in gone]}


def find_record(rid: str) -> dict | None:
    for r in read_history(100000):
        if r["id"] == rid:
            return r
    return None


def job_view(j: dict) -> dict:
    p = j["params"]
    return {"id": j["id"], "status": j["status"], "phase": j["phase"], "progress": j["progress"],
            "progress_max": j["progress_max"], "family": p["family"], "mode": p["mode"],
            "prompt": p["prompt"][:80], "error": j.get("error"), "result": j.get("result"),
            "seed": p["seed"], "peak": j.get("peak")}


def store_upload(raw: bytes) -> dict:
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    name = f"llmapp_{uuid.uuid4().hex}.png"
    INPUT_DIR.mkdir(exist_ok=True)
    img.save(INPUT_DIR / name)
    return {"name": name, "width": img.width, "height": img.height, "url": f"/api/input/{name}"}


# ---------------------------------------------------------------- HTTP handlers

def build_app(state: AppState) -> web.Application:
    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(_):
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @routes.get("/api/ping")
    async def ping(_):
        return web.json_response({"app": APP_SIGNATURE, "pid": os.getpid(), "version": APP_VERSION})

    @routes.get("/api/info")
    async def info(_):
        fams = families()
        return web.json_response({
            "families": fams, "modes": MC.MODES, "size_presets": MC.SIZE_PRESETS,
            "export_sizes": MC.EXPORT_SIZES, "samplers": MC.COMMON_SAMPLERS, "schedulers": MC.COMMON_SCHEDULERS,
            "settings": state.settings, "version": APP_VERSION,
            "textgen_available": (ROOT / "models/text_encoders" / WF.FILES["krea2_turbo"]["clip"]).exists(),
            "installed": {**{k: all((ROOT / "models" / sub / v[key]).exists()
                                    for key, sub in (("unet", "diffusion_models"), ("clip", "text_encoders"), ("vae", "vae")))
                             for k, v in WF.FILES.items()},
                          **{k: (ROOT / "models" / "checkpoints" / v["file"]).exists()
                             for f in ("sdxl", "sd35") for k, v in fams[f]["variants"].items()}},
        })

    @routes.get("/api/tags")
    async def tags(req):
        q = req.query.get("q", "")[:80]
        limit = max(1, min(30, int(req.query.get("limit", "12") or 12)))
        flavor = tag_search.flavor_for(req.query.get("family", ""))
        if not tag_search.available(flavor):
            return web.json_response({"available": False, "results": []})
        return web.json_response({"available": True,
                                  "results": await asyncio.to_thread(tag_search.search, q, limit, flavor)})

    @routes.get("/api/status")
    async def status(_):
        cur = state.current
        return web.json_response({
            "engine": state.comfy.state, "engine_error": state.comfy.error, "engine_port": state.comfy.port,
            "loaded_family": state.loaded_family, "mem": state.mem,
            "current": job_view(cur) if cur else None,
            "queue": [job_view(j) for j in state.queue],
            "recent": [job_view(j) for j in sorted(state.jobs.values(), key=lambda x: -x["created"])[:15]],
        })

    @routes.post("/api/generate")
    async def generate(req):
        body = await req.json()
        try:
            jobs = state.enqueue(body, int(body.get("count", 1)))
        except (ValueError, TypeError) as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        return web.json_response({"ok": True, "jobs": [job_view(j) for j in jobs]})

    @routes.post("/api/cancel/{jid}")
    async def cancel(req):
        return web.json_response({"ok": True, "message": await state.cancel(req.match_info["jid"])})

    @routes.post("/api/upload")
    async def upload(req):
        reader = await req.multipart()
        field = await reader.next()
        if field is None:
            return web.json_response({"ok": False, "error": "파일이 없습니다."}, status=400)
        raw = await field.read(decode=False)
        try:
            return web.json_response({"ok": True, **store_upload(bytes(raw)), "source": field.filename})
        except Exception:
            return web.json_response({"ok": False, "error": "이미지 파일로 읽을 수 없습니다 (PNG/JPG/WEBP 권장)."}, status=400)

    @routes.post("/api/upload_path")
    async def upload_path(req):
        body = await req.json()
        path = Path(str(body.get("path", "")).strip().strip('"'))
        if not path.is_file():
            return web.json_response({"ok": False, "error": f"파일을 찾을 수 없습니다: {path}"}, status=400)
        try:
            return web.json_response({"ok": True, **store_upload(path.read_bytes()), "source": str(path)})
        except Exception:
            return web.json_response({"ok": False, "error": "이미지 파일로 읽을 수 없습니다."}, status=400)

    @routes.post("/api/upload_mask")
    async def upload_mask(req):
        body = await req.json()
        data = str(body.get("data", ""))
        if not data.startswith("data:image/png;base64,"):
            return web.json_response({"ok": False, "error": "마스크 형식이 올바르지 않습니다."}, status=400)
        import base64
        try:
            im = Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1])))
            im.load()
        except Exception:
            return web.json_response({"ok": False, "error": "마스크 이미지를 읽을 수 없습니다."}, status=400)
        # painted strokes are drawn opaque on a transparent canvas → white where alpha > 0
        alpha = im.convert("RGBA").getchannel("A")
        mask = alpha.point(lambda a: 255 if a > 20 else 0).convert("RGB")
        if not mask.getbbox():
            return web.json_response({"ok": False, "error": "칠한 영역이 없습니다. 고칠 곳을 브러시로 칠해 주세요."}, status=400)
        name = f"llmapp_{uuid.uuid4().hex}.png"
        mask.save(INPUT_DIR / name)
        cover = sum(1 for v in alpha.getdata() if v > 20) / (alpha.width * alpha.height)
        return web.json_response({"ok": True, "name": name, "coverage": round(cover, 3)})

    @routes.post("/api/result_as_input/{rid}")
    async def result_as_input(req):
        rec = find_record(req.match_info["rid"])
        if not rec or not Path(rec["path"]).exists():
            return web.json_response({"ok": False, "error": "결과 파일을 찾을 수 없습니다."}, status=404)
        src = rec.get("gen_path") or rec["path"]
        return web.json_response({"ok": True, **store_upload(Path(src).read_bytes()), "source": src})

    @routes.get("/api/input/{name}")
    async def input_file(req):
        name = req.match_info["name"]
        if not re.fullmatch(r"llmapp_[0-9a-f]{32}\.png", name):
            raise web.HTTPNotFound()
        return web.FileResponse(INPUT_DIR / name)

    @routes.get("/api/image/{rid}")
    async def image(req):
        rec = find_record(req.match_info["rid"])
        if not rec or not Path(rec["path"]).exists():
            raise web.HTTPNotFound()
        return web.FileResponse(rec["path"])

    @routes.get("/api/history")
    async def history(req):
        # ?offset=N switches to the paged shape {items,total,offset,limit}; without it the
        # response stays a plain list (newest first) for existing callers.
        try:
            limit = int(req.query.get("limit", 60))
            offset = int(req.query["offset"]) if "offset" in req.query else None
        except ValueError:
            return web.json_response({"ok": False, "error": "offset/limit는 정수여야 합니다."}, status=400)
        if offset is None:
            return web.json_response(read_history(limit))
        return web.json_response(read_history_page(offset, limit))

    @routes.post("/api/history/delete")
    async def history_delete(req):
        body = await req.json()
        if body.get("all"):
            ids = None
        else:
            ids = {str(x) for x in body.get("ids", []) if x}
            if not ids:
                return web.json_response({"ok": False, "error": "삭제할 기록을 고르세요."}, status=400)
        try:
            res = delete_history(ids, bool(body.get("delete_files", False)))
        except OSError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        gone = set(res["ids"])
        for j in state.jobs.values():  # results shown in the status panel must not point to deleted records
            if j.get("result") and j["result"].get("id") in gone:
                j["result"] = None
        log_event("history_deleted", all=ids is None, count=res["deleted"], files=res["files_recycled"])
        return web.json_response({"ok": True, **res})

    @routes.post("/api/open_output")
    async def open_output(req):
        body = await req.json() if req.can_read_body else {}
        target = Path(state.settings["output_dir"])
        if body.get("record"):
            rec = find_record(body["record"])
            if rec:
                target = Path(rec["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            if target.is_file():
                import subprocess
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                target.mkdir(parents=True, exist_ok=True)
                os.startfile(str(target))
        return web.json_response({"ok": True, "path": str(target)})

    @routes.post("/api/open_file/{rid}")
    async def open_file(req):
        rec = find_record(req.match_info["rid"])
        if rec and Path(rec["path"]).exists() and os.name == "nt":
            os.startfile(rec["path"])
            return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": "파일이 없습니다."}, status=404)

    @routes.post("/api/settings")
    async def settings(req):
        body = await req.json()
        if "output_dir" in body:
            path = Path(str(body["output_dir"]).strip().strip('"'))
            try:
                path.mkdir(parents=True, exist_ok=True)
                test = path / ".write_test"
                test.write_text("ok", encoding="utf-8")
                test.unlink()
            except Exception as e:
                return web.json_response({"ok": False, "error": f"이 폴더에 저장할 수 없습니다: {e}"}, status=400)
            state.settings["output_dir"] = str(path)
        save_settings(state.settings)
        return web.json_response({"ok": True, "settings": state.settings})

    @routes.post("/api/unload")
    async def unload(_):
        if state.current:
            return web.json_response({"ok": False, "error": "작업이 끝난 뒤에 해제할 수 있습니다."}, status=409)
        if state.comfy.state == "ready":
            await state.comfy.free(True)
        state.loaded_family = None
        log_event("manual_unload")
        return web.json_response({"ok": True})

    @routes.post("/api/restart_engine")
    async def restart_engine(_):
        if state.current:
            await state.cancel(state.current["id"])
        state.loaded_family = None
        log_event("manual_restart")
        asyncio.create_task(state.comfy.restart())
        return web.json_response({"ok": True})

    @routes.post("/api/shutdown")
    async def shutdown(_):
        log_event("shutdown_requested")
        asyncio.get_running_loop().call_later(0.5, lambda: asyncio.ensure_future(_shutdown(state)))
        return web.json_response({"ok": True})

    @web.middleware
    async def errors(request, handler):
        try:
            resp = await handler(request)
            if request.path.startswith("/static/"):
                resp.headers["Cache-Control"] = "no-cache"  # always pick up app updates
            return resp
        except web.HTTPException:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError):
            return web.json_response({"ok": False, "error": "요청 형식이 올바르지 않습니다(UTF-8 JSON이 아님)."}, status=400)
        except Exception as e:
            log_event("http_error", path=request.path, error=traceback.format_exc()[-2000:])
            return web.json_response({"ok": False, "error": f"앱 내부 오류: {e}"}, status=500)

    app = web.Application(client_max_size=64 * 1024 * 1024, middlewares=[errors])
    app.add_routes(routes)
    app.router.add_static("/static", STATIC)
    return app


async def _shutdown(state: AppState):
    state.shutting_down = True
    await state.comfy.close()
    os._exit(0)


async def serve(port: int, on_ready=None):
    state = AppState()
    state.app_port = port
    app = build_app(state)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)  # localhost only, no public share link
    await site.start()
    log_event("app_started", port=port, pid=os.getpid())
    asyncio.create_task(state.monitor_loop())
    asyncio.create_task(state.worker())
    await state.comfy.start()
    log_event("comfy_started", port=state.comfy.port, state=state.comfy.state, error=state.comfy.error)
    if on_ready:
        on_ready(state)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await state.comfy.close()
        await runner.cleanup()
