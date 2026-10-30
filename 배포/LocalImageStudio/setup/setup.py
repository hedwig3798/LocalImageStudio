"""로컬 이미지 생성기 - 설치 2단계 (bootstrap.ps1이 실행).

환경 점검 → ComfyUI v0.43.0(이전 버전이 있으면 사용자 데이터를 옮기고 교체) → Python 패키지 → 모델 선택·라이선스 동의 → 모델 다운로드 → 바로가기.
다시 실행하면 끝난 단계는 건너뛰고, 중단된 다운로드는 이어받습니다.

옵션(고급/테스트용):
  --groups anima,krea2,qwen21,qwen21_base   모델 묶음 지정(질문 생략)
  --copy-from <폴더>                         이 폴더도 우선 검색해 이미 받은 모델을 가져오기(SHA256 검증)
  --no-search                                다른 폴더 자동 검색을 하지 않기
  --yes                                      라이선스 확인 질문을 '동의'로 처리
  --no-shortcut                              바탕화면 바로가기 만들지 않기
  --skip-models                              모델 단계 건너뛰기
  --check-comfy                              ComfyUI 버전만 확인하고 무엇을 할지 출력(아무것도 바꾸지 않음)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMFY_TAG = "v0.43.0"
COMFY_URL = f"https://github.com/comfyanonymous/ComfyUI/archive/refs/tags/{COMFY_TAG}.zip"
TORCH_INDEX = "https://download.pytorch.org/whl/cu130"
LOCK = ROOT / "setup" / "requirements.lock.txt"
MANIFEST = ROOT / "app" / "tools" / "models_manifest.json"
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
UV = os.environ.get("LIS_UV", str(ROOT / "tools" / "uv" / "uv.exe"))
CACHE = ROOT / ".cache"
GB = 1024 ** 3


def step(n, total, msg):
    print(f"\n==== [{n}/{total}] {msg}", flush=True)


def ask(prompt: str, default: str = "") -> str:
    try:
        ans = input(prompt).strip()
    except EOFError:
        ans = ""
    return ans or default


def fail(msg: str):
    print(f"\n[중단] {msg}", flush=True)
    sys.exit(1)


def run(cmd: list[str], **kw) -> int:
    print("  > " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], **kw).returncode


# ------------------------------------------------------------------ 1. environment
def check_environment(need_bytes: int):
    smi = shutil.which("nvidia-smi")
    if not smi:
        fail("NVIDIA GPU 드라이버(nvidia-smi)를 찾지 못했습니다. 이 앱은 NVIDIA GPU(CUDA)가 필요합니다.")
    q = subprocess.run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True).stdout.strip().splitlines()
    head = subprocess.run([smi], capture_output=True, text=True).stdout
    m = re.search(r"CUDA (?:UMD )?Version:\s*([\d.]+)", head)
    cuda = float(".".join(m.group(1).split(".")[:2])) if m else 0.0
    for line in q:
        name, vram, drv = [x.strip() for x in line.split(",")]
        print(f"  GPU: {name} / VRAM {int(vram) / 1024:.1f}GB / 드라이버 {drv} / CUDA {cuda}")
    vram_mb = max(int(line.split(",")[1]) for line in q)
    if cuda < 13.0:
        fail(f"GPU 드라이버가 지원하는 CUDA 버전({cuda})이 낮습니다. NVIDIA 최신 드라이버(CUDA 13.0 이상 지원)로 "
             "업데이트한 뒤 다시 '설치.bat'을 실행하세요.")
    if vram_mb < 7500:
        print("  [주의] VRAM이 8GB 미만입니다. Krea 2·Qwen은 매우 느리거나 메모리 부족이 날 수 있습니다.")
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = MS()
        ms.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        print(f"  RAM: {ms.ullTotalPhys / GB:.1f}GB")
        if ms.ullTotalPhys < 15 * GB:
            print("  [주의] RAM이 16GB 미만입니다. Krea 2(13.5GB 모델)는 권장하지 않습니다.")
    except Exception:
        pass
    free = shutil.disk_usage(ROOT).free
    print(f"  설치 위치: {ROOT}  (여유 공간 {free / GB:.0f}GB, 필요 약 {need_bytes / GB:.0f}GB)")
    if free < need_bytes:
        fail("디스크 여유 공간이 부족합니다. 공간이 넉넉한 드라이브로 이 폴더를 옮긴 뒤 다시 실행하세요.")


# ------------------------------------------------------------------ 2. ComfyUI
# ComfyUI 폴더 안에서 사용자 데이터로 보고 업데이트 때 옮겨 주는 항목 (나머지는 ComfyUI 프로그램 파일)
COMFY_USER_DIRS = ("input", "output", "user", "custom_nodes", "models")
COMFY_USER_FILES = ("extra_model_paths.yaml",)
_VER_RX = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str):
    m = _VER_RX.search(text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def comfy_version(target: Path):
    """Installed ComfyUI version as ((major, minor, patch) | None, where it was read from).
    Order: comfyui_version.py -> git tag (git clone) -> the installer's .installed_version marker."""
    vf = target / "comfyui_version.py"
    if vf.is_file():
        m = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", vf.read_text(encoding="utf-8", errors="replace"))
        v = parse_version(m.group(1)) if m else None
        if v:
            return v, "comfyui_version.py"
    if (target / ".git").exists() and shutil.which("git"):
        r = subprocess.run(["git", "-C", str(target), "describe", "--tags", "--abbrev=0"],
                           capture_output=True, text=True)
        v = parse_version(r.stdout) if r.returncode == 0 else None
        if v:
            return v, "git tag"
    marker = target / ".installed_version"
    if marker.is_file():
        v = parse_version(marker.read_text(errors="replace"))
        if v:
            return v, ".installed_version"
    return None, ""


def vstr(v) -> str:
    return ".".join(map(str, v)) if v else "알 수 없음"


def comfy_backups(root: Path) -> list[Path]:
    return sorted((p for p in root.glob("ComfyUI_old_*") if p.is_dir()), key=lambda p: p.stat().st_mtime)


def plan_comfyui(root: Path = ROOT) -> tuple[str, object, str]:
    """Decides what to do with root/ComfyUI without changing anything.
    Returns (action, installed_version, message); action is one of
    'ok' (already COMFY_TAG), 'install' (missing / unfinished), 'upgrade' (older), 'keep' (newer or unknown)."""
    target = root / "ComfyUI"
    want = parse_version(COMFY_TAG)
    if not target.exists():
        return "install", None, f"ComfyUI가 없어 {COMFY_TAG}을(를) 새로 설치합니다."
    if not (target / "main.py").exists():
        return "install", None, "ComfyUI 폴더가 완전하지 않습니다(이전 설치가 중간에 끊김). 새로 설치합니다."
    have, src = comfy_version(target)
    if have is None:
        return "keep", None, ("ComfyUI 버전을 확인하지 못했습니다(comfyui_version.py 없음). 직접 설치한 ComfyUI일 수 있어 "
                              "그대로 둡니다. 문제가 생기면 ComfyUI 폴더 이름을 바꾼 뒤 '설치.bat'을 다시 실행하세요.")
    if have == want:
        return "ok", have, f"ComfyUI {vstr(have)} 이미 설치됨 ({src})"
    if have > want:
        return "keep", have, (f"설치된 ComfyUI {vstr(have)}이(가) 이 설치 프로그램이 맞춘 버전({COMFY_TAG})보다 새것입니다. "
                              "그대로 두지만, 맞지 않으면 오류가 날 수 있습니다.")
    return "upgrade", have, f"이전 버전 ComfyUI {vstr(have)}을(를) 찾았습니다. {COMFY_TAG}(으)로 교체합니다."


def _merge_missing(src: Path, dst: Path) -> int:
    """Copies files/folders from src that do not exist in dst (never overwrites the new install's files)."""
    n = 0
    if src.is_file():
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            n += 1
        return n
    for item in src.iterdir():
        if item.name == "__pycache__":
            continue
        n += _merge_missing(item, dst / item.name)
    return n


def restore_user_data(backup: Path, target: Path) -> int:
    """Brings ComfyUI-internal user data (uploaded input images, outputs, ComfyUI user settings,
    extra custom nodes, extra model files, extra_model_paths.yaml) from the old folder into the new one."""
    total = 0
    for name in COMFY_USER_DIRS + COMFY_USER_FILES:
        src = backup / name
        if src.exists():
            n = _merge_missing(src, target / name)
            if n:
                print(f"  - {name}: {n}개 항목을 새 ComfyUI로 옮김")
            total += n
    extra = [p.name for p in (backup / "custom_nodes").glob("*")
             if p.name not in ("__pycache__", "example_node.py.example", "websocket_image_save.py")] \
        if (backup / "custom_nodes").is_dir() else []
    if extra:
        print(f"  [주의] 직접 추가한 커스텀 노드 {len(extra)}개를 옮겼습니다: {', '.join(extra)}\n"
              "         새 ComfyUI 버전과 맞지 않으면 각 노드를 업데이트해야 할 수 있습니다.")
    return total


def _rmtree(path: Path) -> bool:
    def onerror(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerror)
    return not path.exists()


def remove_comfy_backups(root: Path = ROOT):
    """Called only after the new ComfyUI and its packages were installed successfully."""
    for b in comfy_backups(root):
        print(f"  이전 ComfyUI 백업 폴더를 정리합니다: {b.name}")
        if not _rmtree(b):
            print(f"  [안내] '{b.name}' 폴더를 다 지우지 못했습니다. 이미지 생성기를 끈 뒤 직접 지워도 됩니다.")


def fetch_comfy_zip(cache: Path) -> Path:
    local = os.environ.get("LIS_COMFY_ZIP")  # offline/test: use an already downloaded release zip
    if local:
        return Path(local)
    cache.mkdir(exist_ok=True)
    zpath = cache / f"ComfyUI-{COMFY_TAG}.zip"
    print(f"  ComfyUI {COMFY_TAG} 다운로드 중...", flush=True)
    if run(["curl.exe", "-L", "--fail", "--retry", "5", "-C", "-", "-S", "--progress-bar", "-o", zpath, COMFY_URL]) not in (0, 33):
        fail("ComfyUI 다운로드에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요.")
    return zpath


def install_comfyui(root: Path = ROOT) -> str:
    """Installs ComfyUI COMFY_TAG, or replaces an older one (user data inside it is carried over).
    Never touches anything outside root/ComfyUI and its temporary backup root/ComfyUI_old_<version>.
    Returns the plan_comfyui action; 'install'/'upgrade' mean the packages must be re-aligned."""
    target = root / "ComfyUI"
    cache = root / ".cache"
    action, have, msg = plan_comfyui(root)
    leftovers = comfy_backups(root)
    if action in ("ok", "keep"):
        print(("  " if action == "ok" else "  [주의] ") + msg)
        if action == "ok":
            marker = target / ".installed_version"
            if not marker.exists() or marker.read_text(errors="replace").strip() != COMFY_TAG:
                marker.write_text(COMFY_TAG)
            for b in leftovers:  # an earlier upgrade stopped before cleanup: make sure nothing was lost
                print(f"  이전 업데이트에서 남은 백업 '{b.name}'의 사용자 데이터를 확인합니다.")
                restore_user_data(b, target)
        return action

    print("  " + msg)
    zpath = fetch_comfy_zip(cache)
    tmp = cache / "comfy_extract"
    shutil.rmtree(tmp, ignore_errors=True)
    print("  압축 푸는 중...", flush=True)
    try:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        inner = next(tmp.iterdir())
    except (zipfile.BadZipFile, StopIteration, OSError) as e:
        if not os.environ.get("LIS_COMFY_ZIP"):
            Path(zpath).unlink(missing_ok=True)
        fail(f"받은 ComfyUI 파일이 손상되었습니다({e}). '설치.bat'을 다시 실행하세요.")
    got = comfy_version(inner)[0]
    if got != parse_version(COMFY_TAG):
        fail(f"받은 ComfyUI의 버전({vstr(got)})이 {COMFY_TAG}이(가) 아닙니다. '설치.bat'을 다시 실행하세요.")

    backup = None
    if target.exists():
        label = vstr(have) if have else "incomplete"
        backup = root / f"ComfyUI_old_{label}"
        i = 2
        while backup.exists():
            backup = root / f"ComfyUI_old_{label}_{i}"
            i += 1
        print(f"  기존 ComfyUI 폴더를 '{backup.name}'(으)로 옮겨 둡니다 (설치가 끝나면 정리).", flush=True)
        try:
            target.rename(backup)
        except OSError as e:
            fail(f"기존 ComfyUI 폴더를 옮기지 못했습니다({e}).\n"
                 "  이미지 생성기가 켜져 있으면 '이미지생성기_복구.bat'으로 종료하고, ComfyUI 폴더를 연 탐색기·편집기 창을 "
                 "닫은 뒤 '설치.bat'을 다시 실행하세요.")
    shutil.move(str(inner), str(target))
    shutil.rmtree(tmp, ignore_errors=True)
    for b in ([backup] if backup else []) + [b for b in leftovers if b != backup]:
        print(f"  '{b.name}'에서 사용자 데이터(입력 이미지·사용자 설정·추가 노드 등)를 옮기는 중...", flush=True)
        restore_user_data(b, target)
    (target / ".installed_version").write_text(COMFY_TAG)
    print(f"  ComfyUI {COMFY_TAG} 설치 완료")
    return action


# ------------------------------------------------------------------ 3. packages
def install_packages(force: bool = False):
    lock_hash = hashlib.sha256(LOCK.read_bytes()).hexdigest()[:16]
    marker = ROOT / "venv" / ".deps_installed"
    if force:
        print("  ComfyUI가 바뀌어 고정 버전 패키지(requirements.lock.txt)를 다시 맞춥니다.")
    if not force and marker.exists() and marker.read_text().strip() == lock_hash:
        print("  패키지 이미 설치됨")
    else:
        print("  PyTorch(CUDA 13.0) 등 패키지를 설치합니다. 약 3~4GB 다운로드, 수 분~수십 분 걸립니다.")
        rc = run([UV, "pip", "install", "--python", VENV_PY, "-r", LOCK,
                  "--extra-index-url", TORCH_INDEX, "--index-strategy", "unsafe-best-match"])
        if rc != 0:
            fail("패키지 설치에 실패했습니다. 다시 실행하면 이어서 진행합니다.")
        marker.write_text(lock_hash)
    code = ("import torch,sys;ok=torch.cuda.is_available();"
            "print('  torch',torch.__version__,'CUDA 사용 가능' if ok else 'CUDA 사용 불가');sys.exit(0 if ok else 3)")
    if subprocess.run([str(VENV_PY), "-c", code]).returncode != 0:
        fail("PyTorch가 GPU(CUDA)를 사용할 수 없습니다. NVIDIA 드라이버를 최신으로 업데이트하세요.")


# ------------------------------------------------------------------ 4/5. models
MODELS = ROOT / "models"
SKIP_DIRS = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "$windows.~bt",
             "system volume information", "recovery", "perflogs", "msocache", "node_modules", ".git", "__pycache__",
             "site-packages", "appdata", "venv", ".venv", "env", "temp", "tmp"}


def have_file(f) -> bool:
    """A manifest file counts as present when it exists in models/ with the expected size
    (download_models.py still verifies its SHA256 before it is used)."""
    p = MODELS / f["dest"]
    return p.is_file() and p.stat().st_size == f["size"]


def group_status(manifest) -> dict:
    st = {}
    for g in manifest["groups"]:
        files = [f for f in manifest["files"] if g in f["group"].split(",")]
        st[g] = (sum(have_file(f) for f in files), len(files))
    return st


def fixed_drives() -> list[Path]:
    import ctypes
    drives = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if mask & (1 << i):
            root = chr(65 + i) + ":\\"
            if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
                drives.append(Path(root))
    return drives


def search_existing(files: list, extra_roots: list | None = None, time_limit: float = 150) -> dict:
    """Looks for already-downloaded copies of the given manifest files elsewhere on this PC
    (other ComfyUI installs, an older LocalImageStudio folder, Hugging Face cache, ...).
    Match = same file name and same size; the SHA256 is checked later before use.
    Returns {dest: source_path}."""
    import time
    wanted: dict = {}
    for f in files:
        wanted.setdefault(Path(f["dest"]).name.lower(), []).append(f)
    found: dict = {}

    def take(path: Path, size: int):
        for f in wanted.get(path.name.lower(), []):
            if f["dest"] not in found and size == f["size"]:
                found[f["dest"]] = str(path)

    # 1) Hugging Face cache (huggingface_hub layout: models--org--repo/snapshots/<rev>/<path>)
    hubs = [Path(os.environ["HF_HUB_CACHE"])] if os.environ.get("HF_HUB_CACHE") else []
    hubs.append(Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))) / "hub")
    for hub in hubs:
        for f in files:
            for snap in (hub / ("models--" + f["repo"].replace("/", "--")) / "snapshots").glob("*"):
                cand = snap / f["path"]
                try:
                    if cand.exists() and cand.stat().st_size == f["size"]:
                        found.setdefault(f["dest"], str(cand.resolve()))
                except OSError:
                    pass

    # 2) folders on fixed drives (depth-limited, system folders and this install skipped)
    roots = list(extra_roots or []) + [Path.home()] + fixed_drives()
    me = str(ROOT).lower()
    deadline = time.time() + time_limit
    seen = set()
    for root in roots:
        stack = [(root, 0)]
        while stack and time.time() < deadline and len(found) < len(files):
            d, depth = stack.pop()
            key = str(d).lower()
            if key in seen or key.startswith(me):
                continue
            seen.add(key)
            try:
                with os.scandir(d) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                junction = hasattr(e, "is_junction") and e.is_junction()
                                if depth < 7 and not junction and e.name.lower() not in SKIP_DIRS \
                                        and not e.name.startswith("$"):
                                    stack.append((Path(e.path), depth + 1))
                            elif e.name.lower() in wanted:
                                take(Path(e.path), e.stat().st_size)
                        except OSError:
                            pass
            except OSError:
                pass
        if time.time() >= deadline:
            print("  (검색 시간 제한에 도달해 일부 폴더는 확인하지 못했습니다)")
            break
    return found


def choose_models(args, manifest) -> list[str]:
    groups = manifest["groups"]
    sizes = {g: 0 for g in groups}
    for f in manifest["files"]:
        for g in f["group"].split(","):
            sizes[g] += f["size"]
    order = list(groups)
    if args.groups:
        chosen = [g for g in args.groups.split(",") if g in groups]
    else:
        status = group_status(manifest)
        print("  설치할 모델을 고르세요 (번호를 쉼표로 구분, 그냥 엔터 = 1,2,3 / 전부 = all):")
        print("  (이미 설치된 모델은 다시 받지 않습니다. 고르지 않은 모델도 지워지지 않습니다)")
        for i, g in enumerate(order, 1):
            have, total = status[g]
            mark = "  [설치됨]" if have == total else (f"  [일부 설치됨 {have}/{total}]" if have else "")
            print(f"   {i}. {groups[g]['label']}  - 약 {sizes[g] / 1e9:.1f}GB{mark}")
        print("   (Anima와 Krea 2는 VAE 파일 0.25GB를 공유합니다)")
        ans = ask("  선택: ", "1,2,3")
        if ans.strip().lower() in ("all", "전부", "모두"):
            ans = ",".join(str(i) for i in range(1, len(order) + 1))
        chosen = []
        for tok in re.split(r"[,\s]+", ans):
            if tok.isdigit() and 1 <= int(tok) <= len(order):
                chosen.append(order[int(tok) - 1])
        if "qwen21_base" in chosen and "qwen21" not in chosen:
            chosen.append("qwen21")  # Base needs the Qwen text encoder/VAE
        print("  (SDXL을 고르지 않아도 직접 받은 SDXL·SD1.5 체크포인트를 models\\checkpoints에 넣으면 사용할 수 있습니다)")
    chosen = list(dict.fromkeys(chosen))
    if not chosen:
        print("  모델을 고르지 않았습니다. 나중에 '설치.bat'을 다시 실행해 받을 수 있습니다.")
        return []
    print("\n  [라이선스 확인] 모델 가중치는 각 제작사의 라이선스를 따릅니다:")
    for g in chosen:
        print(f"   - {groups[g]['label']}\n     {groups[g]['license']}\n     {groups[g]['license_url']}")
    if not args.yes:
        if ask("  위 라이선스를 확인했고 동의합니다 (y/n): ", "n").lower() not in ("y", "yes", "예", "ㅛ"):
            print("  동의하지 않아 모델 다운로드를 건너뜁니다.")
            return []
    return chosen


def download_models(chosen, manifest, args) -> bool:
    files = [f for f in manifest["files"] if set(f["group"].split(",")) & set(chosen)]
    missing = [f for f in files if not have_file(f)]
    print(f"  선택한 모델 파일 {len(files)}개 중 이 폴더에 이미 있는 파일 {len(files) - len(missing)}개 "
          "(SHA256만 확인하고 다시 받지 않음)")
    sources: dict = {}
    auto = bool(args.yes or os.environ.get("LIS_NO_ASK"))
    if missing and not args.no_search:
        print(f"  나머지 {len(missing)}개를 이 PC의 다른 폴더에서 찾는 중입니다 "
              "(다른 ComfyUI 설치, 이전 설치 폴더, Hugging Face 캐시 등, 최대 약 2분)...", flush=True)
        extra = [Path(args.copy_from)] if args.copy_from else []
        sources = search_existing(missing, extra)
        if sources:
            total = sum(f["size"] for f in missing if f["dest"] in sources)
            print(f"  이미 받은 파일 {len(sources)}개(약 {total / 1e9:.1f}GB)를 찾았습니다:")
            for dest, src in sources.items():
                print(f"   - {dest}  <-  {src}")
            use = "y" if auto else ask("  다운로드 대신 이 파일들을 사용할까요? 같은 드라이브면 공간을 거의 쓰지 않는 "
                                       "하드링크, 다른 드라이브면 복사합니다 (Y/n): ", "y")
            if use.lower() not in ("y", "yes", "예", "ㅛ"):
                sources = {}
        else:
            print("  다른 폴더에서 찾은 파일이 없습니다.")
    still = [f for f in missing if f["dest"] not in sources]
    if still and not auto:
        manual = ask("  USB 등 다른 곳에 받아 둔 모델 폴더가 있으면 경로 입력(없으면 엔터 = 인터넷에서 받기): ", "").strip('"')
        if manual and Path(manual).is_dir():
            print("  입력한 폴더에서 찾는 중...", flush=True)
            more = search_existing(still, [Path(manual)], time_limit=60)
            print(f"  {len(more)}개를 찾았습니다.")
            sources.update(more)
            still = [f for f in missing if f["dest"] not in sources]
    need = sum(f["size"] for f in still)
    copy_need = sum(f["size"] for f in missing if f["dest"] in sources
                    and Path(sources[f["dest"]]).drive.lower() != Path(ROOT).drive.lower())
    free = shutil.disk_usage(ROOT).free
    print(f"  인터넷에서 받을 파일 {len(still)}개, 약 {need / 1e9:.1f}GB / 다른 드라이브에서 복사할 양 약 "
          f"{copy_need / 1e9:.1f}GB (여유 공간 {free / 1e9:.0f}GB)")
    if need + copy_need + 2 * GB > free:
        fail("디스크 여유 공간이 부족합니다. 모델을 줄여 고르거나 공간을 비운 뒤 다시 실행하세요.")
    CACHE.mkdir(exist_ok=True)
    src_file = CACHE / "model_sources.json"
    src_file.write_text(json.dumps(sources, ensure_ascii=False, indent=1), encoding="utf-8")
    cmd = [VENV_PY, "-X", "utf8", ROOT / "app" / "tools" / "download_models.py", "--models-dir", MODELS,
           "--group", ",".join(chosen), "--sources", src_file]
    return run(cmd) == 0


# ------------------------------------------------------------------ 6. shortcuts
def make_shortcuts():
    ps = r"""
$d=[Environment]::GetFolderPath('Desktop'); $w=New-Object -ComObject WScript.Shell
$a=$w.CreateShortcut((Join-Path $d '로컬 이미지 생성기.lnk')); $a.TargetPath=$env:LIS_ROOT+'\이미지생성기_실행.bat'
$a.WorkingDirectory=$env:LIS_ROOT; $a.IconLocation='C:\Windows\System32\imageres.dll,67'; $a.Save()
$b=$w.CreateShortcut((Join-Path $d '로컬 이미지 생성기 - 종료·복구.lnk')); $b.TargetPath=$env:LIS_ROOT+'\이미지생성기_복구.bat'
$b.WorkingDirectory=$env:LIS_ROOT; $b.IconLocation='C:\Windows\System32\imageres.dll,93'; $b.Save()
Write-Host ('  바탕화면 바로가기를 만들었습니다: '+$d)
"""
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                   env=dict(os.environ, LIS_ROOT=str(ROOT)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default="")
    ap.add_argument("--copy-from", default="")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--no-shortcut", action="store_true")
    ap.add_argument("--skip-models", action="store_true")
    ap.add_argument("--no-search", action="store_true", help="do not look for already-downloaded models elsewhere")
    ap.add_argument("--check-comfy", action="store_true", help="only report what would happen to ComfyUI (dry run)")
    args = ap.parse_args()
    if args.check_comfy:
        action, have, msg = plan_comfyui(ROOT)
        print(f"[ComfyUI 확인] 설치된 버전: {vstr(have)} / 목표: {COMFY_TAG} / 동작: {action}\n  {msg}")
        sys.exit(0)
    if args.groups or args.copy_from:
        os.environ["LIS_NO_ASK"] = "1"
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    total = 6
    print(f"설치 폴더: {ROOT}")

    step(1, total, "PC 환경 점검")
    check_environment(8 * GB)  # program part; model space is checked after choosing models

    step(2, total, f"ComfyUI {COMFY_TAG} 설치·버전 확인 (이전 버전이면 교체)")
    comfy_action = install_comfyui()

    step(3, total, "Python 패키지 설치 (PyTorch CUDA 13.0)")
    install_packages(force=comfy_action in ("install", "upgrade"))
    if comfy_action != "keep" and comfy_backups(ROOT):
        remove_comfy_backups(ROOT)  # new ComfyUI + packages are in place: the old copy is no longer needed

    models_ok = True
    if args.skip_models:
        step(4, total, "모델 선택 (건너뜀)")
    else:
        step(4, total, "모델 선택과 라이선스 확인")
        chosen = choose_models(args, manifest)
        step(5, total, "모델 확인·다운로드 (이미 있는 모델은 건너뜀, 이어받기 + SHA256 검증)")
        if chosen:
            models_ok = download_models(chosen, manifest, args)
        else:
            print("  건너뜀")

    for sub in ("checkpoints", "loras"):  # folders for user-added SD checkpoints / LoRAs
        (ROOT / "models" / sub).mkdir(parents=True, exist_ok=True)

    step(6, total, "바탕화면 바로가기")
    if args.no_shortcut:
        print("  건너뜀(--no-shortcut)")
    else:
        make_shortcuts()

    if shutil.which(UV) or Path(UV).exists():
        subprocess.run([UV, "cache", "clean"], capture_output=True)  # 설치용 임시 캐시 정리(수 GB)
    (CACHE / f"ComfyUI-{COMFY_TAG}.zip").unlink(missing_ok=True)

    print("\n" + "=" * 60)
    if models_ok:
        print("설치가 끝났습니다. 바탕화면의 '로컬 이미지 생성기'로 실행하세요.")
    else:
        print("일부 모델 다운로드가 실패했습니다. '설치.bat'을 다시 실행하면 이어받습니다.")
    print("=" * 60)
    sys.exit(0 if models_ok else 2)


if __name__ == "__main__":
    main()
