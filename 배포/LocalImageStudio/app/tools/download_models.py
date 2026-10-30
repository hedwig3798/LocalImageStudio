"""Resumable model downloader with SHA256 verification.

Usage: python download_models.py [--models-dir DIR] [--group anima,krea2,qwen21] [--verify-only]

Files are downloaded to <dest>.part with `curl -C -` (resume), checked against the
Hugging Face LFS sha256, and only then renamed to the final name. Results are written
to <models-dir>/download_log.jsonl and <models-dir>/hashes.json.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(16 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def log(models_dir: Path, rec: dict):
    rec["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    print(json.dumps(rec, ensure_ascii=False), flush=True)
    with open(models_dir / "download_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=str(HERE.parent.parent / "models"))
    ap.add_argument("--group", default="")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--copy-from", default="", help="existing models folder to hard-link/copy from (still SHA256-checked)")
    ap.add_argument("--sources", default="", help="JSON {dest: existing file path} found by setup.py (still SHA256-checked)")
    args = ap.parse_args()
    sources = json.loads(Path(args.sources).read_text(encoding="utf-8")) if args.sources else {}

    models_dir = Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((HERE / "models_manifest.json").read_text(encoding="utf-8"))
    groups = {g for g in args.group.split(",") if g}
    hashes_path = models_dir / "hashes.json"
    hashes = json.loads(hashes_path.read_text(encoding="utf-8")) if hashes_path.exists() else {}

    failed = 0
    for item in manifest["files"]:
        if groups and not groups & set(item["group"].split(",")):
            continue
        dest = models_dir / item["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{item['repo']}/resolve/{item['revision']}/{item['path']}"

        if dest.exists():
            known = hashes.get(item["dest"], {})
            if known.get("sha256") == item["sha256"] and known.get("size") == dest.stat().st_size:
                log(models_dir, {"file": item["dest"], "status": "already_verified"})
                continue
            print(f"이미 있는 파일 SHA256 확인 중: {item['dest']} ({dest.stat().st_size / 1e9:.1f}GB)", flush=True)
            digest = sha256_of(dest)
            if digest == item["sha256"]:
                hashes[item["dest"]] = {"sha256": digest, "size": dest.stat().st_size, "url": url,
                                        "repo": item["repo"], "revision": item["revision"], "verified": True}
                hashes_path.write_text(json.dumps(hashes, indent=2), encoding="utf-8")
                log(models_dir, {"file": item["dest"], "status": "verified_existing"})
                continue
            log(models_dir, {"file": item["dest"], "status": "hash_mismatch_existing", "got": digest})
            dest.rename(dest.with_suffix(dest.suffix + ".bad"))

        if args.verify_only:
            log(models_dir, {"file": item["dest"], "status": "missing"})
            failed += 1
            continue

        part = dest.with_name(dest.name + ".part")
        ok = False
        digest = None
        src = None
        if item["dest"] in sources:
            src = Path(sources[item["dest"]])
        elif args.copy_from:
            src = Path(args.copy_from) / item["dest"]
        if src and src.is_file() and src.stat().st_size == item.get("size", src.stat().st_size):
            part.unlink(missing_ok=True)
            try:
                os.link(src, part)          # same drive: instant, no extra space
                how = "hardlink"
            except OSError:
                print(f"\n복사 중: {src} ({src.stat().st_size / 1e9:.1f}GB)", flush=True)
                shutil.copyfile(src, part)
                how = "copy"
            print(f"SHA256 확인 중(이미 받은 파일 사용): {item['dest']}", flush=True)
            if sha256_of(part) == item["sha256"]:
                ok = True
                digest = item["sha256"]
                log(models_dir, {"file": item["dest"], "status": "reused_local", "how": how, "from": str(src)})
            else:  # wrong/corrupt copy elsewhere: fall back to downloading
                part.unlink(missing_ok=True)
                log(models_dir, {"file": item["dest"], "status": "local_hash_mismatch", "from": str(src)})
        if not ok:
            print(f"\n다운로드: {item['dest']} ({item.get('size', 0) / 1e9:.2f}GB) - 중단돼도 다시 실행하면 이어받습니다", flush=True)
        for attempt in range(1, 4 if not ok else 1):  # bounded retries, no infinite loop
            t0 = time.time()
            r = subprocess.run(["curl.exe", "-L", "--fail", "-C", "-", "--retry", "5", "--retry-delay", "5",
                                "--speed-limit", "10000", "--speed-time", "120",
                                "--progress-bar", "-S", "-o", str(part), url])
            if r.returncode in (0, 33) and part.exists():  # 33 = range not satisfiable (already complete)
                ok = True
                log(models_dir, {"file": item["dest"], "status": "downloaded", "attempt": attempt,
                                 "seconds": round(time.time() - t0, 1), "bytes": part.stat().st_size})
                break
            log(models_dir, {"file": item["dest"], "status": "curl_error", "code": r.returncode, "attempt": attempt})
        if not ok:
            failed += 1
            continue

        if digest is None:
            print(f"SHA256 확인 중: {item['dest']}", flush=True)
            digest = sha256_of(part)
        if digest != item["sha256"]:
            log(models_dir, {"file": item["dest"], "status": "hash_mismatch", "got": digest, "expected": item["sha256"]})
            part.rename(part.with_suffix(".bad"))
            failed += 1
            continue
        os.replace(part, dest)
        hashes[item["dest"]] = {"sha256": digest, "size": dest.stat().st_size, "url": url,
                                "repo": item["repo"], "revision": item["revision"], "verified": True}
        hashes_path.write_text(json.dumps(hashes, indent=2), encoding="utf-8")
        log(models_dir, {"file": item["dest"], "status": "verified", "sha256": digest})

    print("FAILED:", failed)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
