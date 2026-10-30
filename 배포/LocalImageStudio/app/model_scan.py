"""Finds user checkpoints / LoRAs in models/ and classifies them by architecture.

Only the safetensors JSON header is read (a few KB), never the weights.
Results are cached by (path, size, mtime).
"""
from __future__ import annotations

import copy
import json
import re
import struct
from pathlib import Path

ARCH_LABEL = {"sdxl": "SDXL", "sd15": "SD1.5", "sd2": "SD2.x", "sd3": "SD3.x",
              "sd35_medium": "SD3.5 Medium", "sd35_large": "SD3.5 Large", "flux": "Flux.1", "anima": "Anima",
              "krea2": "Krea 2", "qwen21": "Qwen-Image 2.1", "moss": "MOSS-Image", "qwen_image": "Qwen-Image 1.x",
              "sdxl_refiner": "SDXL Refiner", "unknown": "알 수 없음"}
_cache: dict[tuple, dict] = {}
# Anima DiT blocks ("blocks.N.self_attn.q_proj"; Wan uses ".q", SD3/Flux use other block names) or its LLM adapter
_ANIMA_KEY = re.compile(r"(^|[._])(blocks[._]\d+[._](self|cross)_attn[._]q_proj|llm_adapter[._])")
# Krea 2 DiT: native "blocks.N.attn.wq|wk|wv|wo|gate" / "blocks.N.mlp.gate|up|down" / "txtfusion.",
# diffusers (comfy.utils.krea2_to_diffusers) "transformer_blocks.N.attn.to_gate" / "ff.gate|up|down" / "text_fusion.",
# kohya "lora_unet_blocks_N_attn_wq" / "lora_unet_txtfusion_"
_KREA2_KEY = re.compile(r"(^|[._])(blocks[._]\d+[._](attn[._](wq|wk|wv|wo|gate)|mlp[._](gate|up|down))[._]"
                        r"|txtfusion[._]|text_fusion[._]|attn[._]to_gate[._]|ff[._](gate|up|down)[._])")
# MOSS-Image DiT: "layers.N.proj.q|k|v|o|qkv|ffn_up_gate|ffn_down" (official/ai-toolkit, kohya "lora_unet_layers_N_proj_q"),
# its DiT-side text embedder "text_embedder.blocks.N." and "shared_block_modulation"
_MOSS_KEY = re.compile(r"(^|[._])(layers[._]\d+[._]proj[._](q|k|v|o|qkv|ffn_up_gate|ffn_down)[._]"
                       r"|text_embedder[._]blocks[._]\d+[._]|shared_block_modulation[._])")
# Qwen-Image MMDiT: "transformer_blocks.N.img_mlp|txt_mlp|img_mod|txt_mod" (diffusers/kohya/lycoris spellings)
_QWEN_KEY = re.compile(r"transformer_blocks[._]\d+[._](img_mlp|txt_mlp|img_mod|txt_mod)[._]")
# Flux.1 (not usable on Krea 2): "double_blocks"/"single_blocks" or diffusers "single_transformer_blocks"
_FLUX_KEY = re.compile(r"(^|[._])(double_blocks|single_blocks|single_transformer_blocks)[._]")
# SD3 MMDiT diffusers keys that Qwen-Image does not have
_SD3_KEY = re.compile(r"transformer_blocks[._]\d+[._](ff_context|norm1_context|attn[._]to_add_out)[._]")
_BLOCK_IDX = re.compile(r"transformer_blocks[._](\d+)[._]")
_TO_Q_DOWN = re.compile(r"attn[._]to_q\.(lora_A|lora_down)\.weight$")
# SD3 MMDiT block index: kohya "lora_unet_joint_blocks_N_", ComfyUI/original "joint_blocks.N.",
# diffusers "transformer_blocks.N."
_SD3_BLOCK = re.compile(r"(?:joint_blocks|transformer_blocks)[._](\d+)[._]")
# LoRA factor weights (lora_down/lora_up, PEFT lora_A/lora_B, or plain weights of a full checkpoint)
_SD3_FACTOR = re.compile(r"\.(lora_down|lora_up|lora_A|lora_B)\.weight$|\.weight$")
# hidden size: SD3/3.5 Medium 1536 (24 blocks), SD3.5 Large 2432 (38 blocks)
SD3_HIDDEN = {1536: "sd35_medium", 2432: "sd35_large"}
SD3_ARCHS = ("sd3", "sd35_medium", "sd35_large")


def _to_q_width(h: dict, keys: list[str]) -> int:
    """in_features of an attention to_q LoRA (lora_A / lora_down is [rank, in_features]); 0 if not found.
    SD3.5 Medium 1536, Large 2432, Qwen-Image 1.x / Flux.1 3072, Qwen-Image 2.1 4096, Krea 2 6144."""
    for k in keys:
        if _TO_Q_DOWN.search(k):
            shape = h[k].get("shape") or []
            if len(shape) == 2:
                return int(shape[1])
    return 0


def _qwen_generation(h: dict, keys: list[str]) -> str:
    """Qwen-Image 2.1 (7B: 32 blocks, width 4096) vs 1.x/2512/Edit (20B: 60 blocks, width 3072)."""
    w = _to_q_width(h, keys)
    if w:
        return "qwen_image" if w == 3072 else "qwen21"
    idx = [int(m.group(1)) for k in keys for m in [_BLOCK_IDX.search(k)] if m]
    return "qwen_image" if idx and max(idx) >= 32 else "qwen21"


def _sd3_size(h: dict, keys: list[str]) -> str:
    """SD3-like weights -> "sd35_medium" | "sd35_large" from the hidden size seen in the joint-block
    attention/MLP/adaLN shapes (any LoRA factor or plain weight dimension equal to 1536 / 2432), falling back to
    the block count (Large has 38 blocks, Medium 24). "sd3" when it cannot be told."""
    seen = {}
    for k in keys:
        if not _SD3_BLOCK.search(k) or not _SD3_FACTOR.search(k):
            continue
        for d in h[k].get("shape") or []:
            if d in SD3_HIDDEN:
                seen[d] = seen.get(d, 0) + 1
    if len(seen) == 1:
        return SD3_HIDDEN[next(iter(seen))]
    if len(seen) > 1:  # both sizes present (should not happen): take the clearly dominant one
        (d1, n1), (_, n2) = sorted(seen.items(), key=lambda kv: -kv[1])[:2]
        return SD3_HIDDEN[d1] if n1 >= 4 * n2 else "sd3"
    idx = [int(m.group(1)) for k in keys for m in [_SD3_BLOCK.search(k)] if m]
    top = max(idx, default=-1)
    if top >= 24:
        return "sd35_large"
    if top == 23:
        return "sd35_medium"
    return "sd3"


def read_header(path: Path) -> dict:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 100_000_000:
            raise ValueError("header too large")
        return json.loads(f.read(n))


def _info(path: Path) -> dict:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime)
    if key in _cache:
        return _cache[key]
    info = {"arch": "unknown", "dtype": "", "size": st.st_size, "meta": {}}
    try:
        h = read_header(path)
        meta = h.pop("__metadata__", {}) or {}
        keys = list(h)
        info["meta"] = {k: meta[k] for k in ("modelspec.architecture", "ss_base_model_version", "modelspec.title") if k in meta}
        dts = {}
        for k in keys[:400]:
            dt = h[k].get("dtype")
            dts[dt] = dts.get(dt, 0) + 1
        info["dtype"] = max(dts, key=dts.get) if dts else ""

        def has(prefix):
            return any(k.startswith(prefix) for k in keys)

        arch_meta = (meta.get("modelspec.architecture", "") + " " + meta.get("ss_base_model_version", "")).lower()
        # transformer_blocks with index >= 38 can only be Qwen-Image 1.x (60 blocks); SD3.5 Large has 38
        max_tb = max((int(m.group(1)) for k in keys for m in [_BLOCK_IDX.search(k)] if m), default=-1)
        # attention-only diffusers LoRAs ("transformer_blocks.N.attn.to_q") look alike; tell them apart by width
        q_w = _to_q_width(h, keys) if max_tb >= 0 else 0
        sd3_keys = any(_SD3_KEY.search(k) for k in keys)
        flux_keys = "flux" in arch_meta or any(_FLUX_KEY.search(k) for k in keys)
        # SD3 MMDiT: full checkpoints "model.diffusion_model.joint_blocks.", ComfyUI LoRAs "diffusion_model.joint_blocks.",
        # kohya "lora_unet_joint_blocks_" (checked first: kohya SD3 LoRAs may also carry "lora_te2_" CLIP-G keys)
        if any("joint_blocks" in k for k in keys):
            info["arch"] = _sd3_size(h, keys)
        elif has("model.diffusion_model.double_blocks") or has("double_blocks"):
            info["arch"] = "flux"
        elif has("conditioner.embedders.1"):
            info["arch"] = "sdxl"
        elif has("conditioner.embedders.0"):
            info["arch"] = "sdxl_refiner"
        elif has("cond_stage_model.transformer"):
            info["arch"] = "sd15"
        elif has("cond_stage_model.model"):
            info["arch"] = "sd2"
        # LoRA files (no full model keys)
        # Anima (Cosmos-Predict2 DiT): kohya "lora_unet_blocks_N_self_attn_q_proj" / "lora_unet_llm_adapter_",
        # generic/diffusers "diffusion_model.blocks.N.cross_attn.q_proj". Checked before SD1.5 because kohya
        # Anima LoRAs also use the "lora_te_" prefix (Qwen3 text encoder).
        elif "anima" in arch_meta or "cosmos" in arch_meta or any(_ANIMA_KEY.search(k) for k in keys):
            info["arch"] = "anima"
        elif "moss" in arch_meta or any(_MOSS_KEY.search(k) for k in keys):
            info["arch"] = "moss"
        elif "krea" in arch_meta or any(_KREA2_KEY.search(k) for k in keys) or q_w == 6144:
            info["arch"] = "krea2"
        elif ("qwen" in arch_meta or any(_QWEN_KEY.search(k) for k in keys)
              or (not sd3_keys and not flux_keys and (max_tb >= 38 or q_w in (3072, 4096)))):
            info["arch"] = _qwen_generation(h, keys)
        elif flux_keys:
            info["arch"] = "flux"
        elif has("lora_te2_") or "xl" in arch_meta:
            info["arch"] = "sdxl"
        elif ("sd3" in arch_meta or "stable-diffusion-3" in arch_meta or has("lora_unet_joint_blocks")
              or has("transformer.transformer_blocks") or sd3_keys):
            info["arch"] = _sd3_size(h, keys)
        elif has("lora_te_") or has("lora_unet_down_blocks") or "sd_v1" in arch_meta or "v1-" in arch_meta:
            info["arch"] = "sd15"
    except Exception as e:  # noqa: BLE001 - unreadable file is shown as unknown
        info["error"] = str(e)[:200]
    _cache[key] = info
    return info


def scan(models_dir: Path, exclude: set[str]) -> dict:
    out = {"checkpoints": [], "loras": []}
    for kind in ("checkpoints", "loras"):
        base = models_dir / kind
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.safetensors")):
            rel = p.relative_to(base).as_posix()
            if rel in exclude:
                continue
            i = _info(p)
            out[kind].append({"name": rel, "arch": i["arch"], "arch_label": ARCH_LABEL.get(i["arch"], i["arch"]),
                              "dtype": i["dtype"], "size_gb": round(i["size"] / 1e9, 2)})
    return out


def sd_families(families: dict, models_dir: Path, app_loras: set[str]) -> tuple[dict, dict]:
    """Returns (families with SD variants/LoRAs filled in, raw scan)."""
    fams = copy.deepcopy(families)
    found = scan(models_dir, exclude=app_loras)
    sdxl = fams.get("sdxl")
    if sdxl:
        variants = {}
        for c in found["checkpoints"]:
            if c["arch"] not in ("sdxl", "sd15"):
                continue
            official = c["name"] == "sd_xl_base_1.0.safetensors"
            variants[f"ckpt:{c['name']}"] = {
                "label": ("SDXL Base 1.0 (공식)" if official else c["name"].rsplit(".", 1)[0]) + f" · {c['arch_label']}",
                "precision": f"{c['arch_label']} 체크포인트 {c['size_gb']}GB ({c['dtype']})",
                "negative": True, "arch": c["arch"], "lora_archs": [c["arch"]], "file": c["name"],
                "default_size": [1024, 1024] if c["arch"] == "sdxl" else [512, 768],
            }
        # official base first
        sdxl["variants"] = dict(sorted(variants.items(), key=lambda kv: (kv[1]["file"] != "sd_xl_base_1.0.safetensors", kv[0])))
        default = next(iter(sdxl["variants"]), "")
        for pr in sdxl["presets"].values():
            pr["variant"] = default
        sdxl["unsupported_checkpoints"] = [c for c in found["checkpoints"]
                                           if c["arch"] not in ("sdxl", "sd15") + SD3_ARCHS]
    sd35 = fams.get("sd35")
    if sd35:
        for v in sd35["variants"].values():
            v["arch"] = "sd3"  # graph type (workflows.build_sd); LoRA matching uses the variant's "lora_archs"
    # User LoRAs for every family that declares "lora" (models_config): LoRAs of its "lora_archs",
    # plus unknown-arch files (shown everywhere; a mismatch only produces 'lora key not loaded' warnings).
    # A variant may narrow this with its own "lora_archs" (SD 3.5 Medium vs Large, SDXL vs SD1.5 checkpoint);
    # the GUI filters by it and server.normalize rejects a LoRA of another variant.
    for f in fams.values():
        if f.get("lora"):
            archs = set(f.get("lora_archs") or ())
            f["loras"] = [l for l in found["loras"] if l["arch"] in archs or l["arch"] == "unknown"]
    return fams, found


def lora_fits_variant(lora: dict, variant: dict) -> bool:
    """True when a scanned LoRA ({"arch": ...}) may be used with this variant (unknown arch always may)."""
    archs = variant.get("lora_archs")
    return not archs or lora["arch"] == "unknown" or lora["arch"] in archs
