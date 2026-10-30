"""Danbooru tag autocomplete for the prompt boxes.

Source: https://huggingface.co/datasets/mengod-hf/danbooru-tags-kr (MIT, data/tags/LICENSE.txt).
CSV rows: tag, category, post count, "[분류 > 세부] 설명. 키워드: <그룹>, 별칭1, 별칭2"
Two prompt-ready variants are used as-is: "comfyui" (parentheses escaped as \\( \\)) for SD/Krea/Qwen, and
"anima" (same, plus '@' in front of artist tags) for Anima. Matching ignores the escapes and the artist '@'.
"""
from __future__ import annotations

import csv
import heapq
import re
import threading
from pathlib import Path

TAG_DIR = Path(__file__).resolve().parent / "data" / "tags"
CATEGORIES = {0: "일반", 1: "작가", 3: "작품", 4: "캐릭터", 5: "메타"}
FLAVORS = ("comfyui", "anima")

_lock = threading.Lock()
# flavor -> [(tag, category, count, korean_name, aliases_lower, path, description, " "+key, key, joined aliases)]
_tags: dict[str, list[tuple]] = {}


def flavor_for(family: str) -> str:
    return "anima" if family == "anima" else "comfyui"


def _csv_file(flavor: str) -> Path | None:
    files = sorted(TAG_DIR.glob(f"danbooru-tags-{flavor}-kr-*.csv"))
    return files[-1] if files else None


def _parse(desc: str) -> tuple[str, list[str]]:
    path = desc[1:desc.index("]")] if desc.startswith("[") and "]" in desc else ""
    aliases = []
    if "키워드:" in desc:
        for a in desc.split("키워드:", 1)[1].split(","):
            a = a.strip()
            if a and not a.startswith("<"):
                aliases.append(a)
    return path, aliases


def _key(tag: str, cat: int) -> str:
    """Plain lowercase form used for matching: no \\( escapes, no artist '@' (general tags like '@ @' keep it)."""
    k = tag.replace("\\(", "(").replace("\\)", ")").lower()
    return k[1:] if cat == 1 and k.startswith("@") else k


def load(flavor: str) -> list[tuple]:
    with _lock:
        if flavor not in _tags:
            out = []
            f = _csv_file(flavor)
            if f:
                with open(f, encoding="utf-8", newline="") as fh:
                    for row in csv.reader(fh):
                        if len(row) != 4:
                            continue
                        tag, cat, cnt, desc = row
                        cat = int(cat or 0)
                        path, aliases = _parse(desc)
                        low = [a.lower() for a in aliases]
                        key = _key(tag, cat)
                        out.append((tag, cat, int(cnt or 0), aliases[0] if aliases else "",
                                    low, path, desc, " " + key, key, "\n".join(low)))
            _tags[flavor] = out
        return _tags[flavor]


def available(flavor: str = "comfyui") -> bool:
    return _csv_file(flavor) is not None


def normalize(q: str) -> str:
    q = q.replace("\\(", "(").replace("\\)", ")").replace("_", " ")
    return re.sub(r"\s+", " ", q.strip().lstrip("@").strip()).lower()


def search(query: str, limit: int = 12, flavor: str = "comfyui") -> list[dict]:
    q = normalize(query)
    if not q:
        return []
    word_q = " " + q
    tags = load(flavor)
    ranked = []
    for i, (_tag, _cat, cnt, _ko, aliases, _path, _desc, spaced, key, joined) in enumerate(tags):
        # lower rank = better: tag prefix, word prefix in tag, Korean alias prefix, substring anywhere
        if key.startswith(q):
            rank, hit = 0, ""
        elif word_q in spaced:
            rank, hit = 1, ""
        elif q in key:
            rank, hit = 3, ""
        elif q in joined:
            hit = next((a for a in aliases if a.startswith(q)), "")
            rank = 2 if hit else 3
            hit = hit or next((a for a in aliases if q in a), "")
        else:
            continue
        ranked.append((rank, -cnt, i, hit))
    out = []
    for rank, _neg, i, hit in heapq.nsmallest(limit, ranked):
        tag, cat, cnt, ko, _aliases, path, desc = tags[i][:7]
        out.append({"tag": tag, "category": cat, "category_label": CATEGORIES.get(cat, ""), "count": cnt,
                    "korean": ko, "matched_alias": hit if hit and hit != ko.lower() else "",
                    "path": path, "description": desc})
    return out
