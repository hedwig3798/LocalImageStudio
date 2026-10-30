"""Builds ComfyUI API-format graphs for each model and mode.

Every builder returns (graph, meta). meta["phases"] maps node ids to the Korean
phase name shown in the GUI, and meta["output_node"] is the node whose image is
fetched. Node structures follow the official Comfy-Org workflow templates
(ComfyUI v0.43.0, templates 0.11.97); see docs/워크플로.md.
"""
from __future__ import annotations

QWEN_TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"

# Files used per model variant (all under <install folder>/models)
FILES = {
    "anima_turbo": {"unet": "anima-turbo-v1.1.safetensors", "clip": "qwen_3_06b_base.safetensors",
                    "clip_type": "stable_diffusion", "vae": "qwen_image_vae.safetensors"},
    "anima_aesthetic": {"unet": "anima-aesthetic-v1.1.safetensors", "clip": "qwen_3_06b_base.safetensors",
                        "clip_type": "stable_diffusion", "vae": "qwen_image_vae.safetensors"},
    "krea2_turbo": {"unet": "krea2_turbo_int8_convrot.safetensors", "clip": "qwen3vl_4b_fp8_scaled.safetensors",
                    "clip_type": "krea2", "vae": "qwen_image_vae.safetensors",
                    "style_lora": "krea2_style_reference.safetensors"},
    "qwen21_turbo": {"unet": "qwen_image_2.1_turbo_int8_convrot.safetensors",
                     "clip": "qwen3vl_8b_int8_convrot.safetensors", "clip_type": "qwen_image",
                     "vae": "qwen_image_2.1_vae_bf16.safetensors"},
    "qwen21_base": {"unet": "qwen_image_2.1_int8_convrot.safetensors",
                    "clip": "qwen3vl_8b_int8_convrot.safetensors", "clip_type": "qwen_image",
                    "vae": "qwen_image_2.1_vae_bf16.safetensors"},
    "moss_sft": {"unet": "moss_image_sft_int8_convrot.safetensors", "clip": "qwen3.5_9b_w4a8_gptq.safetensors",
                 "clip_type": "moss_image", "vae": "flux2-vae_small.safetensors"},
}

PHASE_LOAD = "모델 로딩"
PHASE_ENCODE = "프롬프트·이미지 인코딩"
PHASE_SAMPLE = "이미지 생성(샘플링)"
PHASE_DECODE = "VAE 디코딩"
PHASE_TEXTGEN = "프롬프트 보강/번역(로컬 LLM)"


class G:
    """Tiny helper to assemble an API graph."""

    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.phases: dict[str, str] = {}
        self._n = 0

    def add(self, class_type: str, phase: str | None = None, **inputs) -> str:
        self._n += 1
        nid = str(self._n)
        self.nodes[nid] = {"class_type": class_type, "inputs": inputs}
        if phase:
            self.phases[nid] = phase
        return nid


def out(nid: str, idx: int = 0):
    return [nid, idx]


def _load_image(g: G, name: str) -> str:
    return g.add("LoadImage", PHASE_ENCODE, image=name)


def _fit_image(g: G, img: str, width: int, height: int) -> str:
    # Center-crop + resize the input to the exact generation size so the
    # latent matches the requested width/height.
    return g.add("ImageScale", PHASE_ENCODE, image=out(img), upscale_method="lanczos",
                 width=width, height=height, crop="center")


def _encode(g: G, img: str, vae, tiled: bool) -> str:
    """VAE encode; tiled keeps activation memory small (untiled SDXL encode stalled on this 8GB GPU)."""
    if tiled:
        return g.add("VAEEncodeTiled", PHASE_ENCODE, pixels=out(img), vae=vae, tile_size=512, overlap=64,
                     temporal_size=64, temporal_overlap=8)
    return g.add("VAEEncode", PHASE_ENCODE, pixels=out(img), vae=vae)


def _sampler_ksampler(g, model, pos, neg, latent, p, denoise):
    """model is a link ([node, slot]); pos/neg/latent are node ids."""
    return g.add("KSampler", PHASE_SAMPLE, model=model, positive=out(pos), negative=out(neg),
                 latent_image=out(latent), seed=p["seed"], steps=p["steps"], cfg=p["cfg"],
                 sampler_name=p["sampler"], scheduler=p["scheduler"], denoise=denoise)


def _chain_loras(g: G, model, clip, loras: list[dict], model_only: bool = False):
    """User LoRA chain. model_only=False: LoraLoader (diffusion model + text encoder, same strength);
    model_only=True: LoraLoaderModelOnly (clip is passed through unchanged)."""
    for lo in loras:
        if model_only:
            ll = g.add("LoraLoaderModelOnly", PHASE_LOAD, model=model, lora_name=lo["name"],
                       strength_model=lo["strength"])
            model = out(ll, 0)
        else:
            ll = g.add("LoraLoader", PHASE_LOAD, model=model, clip=clip, lora_name=lo["name"],
                       strength_model=lo["strength"], strength_clip=lo["strength"])
            model, clip = out(ll, 0), out(ll, 1)
    return model, clip


def user_loras(g: G, p: dict, model, clip=None):
    """Inserts the user's LoRAs (p["loras"]) right after the model/text-encoder loaders.

    How they are applied comes from the family declaration in models_config ("lora": "model_clip" |
    "model_only"), copied to p["lora_apply"] by the server. Without it: model+clip when a clip is given,
    otherwise model-only. Returns (model, clip) links."""
    loras = p.get("loras") or []
    if not loras:
        return model, clip
    mode = p.get("lora_apply") or ("model_clip" if clip is not None else "model_only")
    return _chain_loras(g, model, clip, loras, model_only=(mode == "model_only" or clip is None))


def _finish(g: G, samples: str, vae: str, tiled: bool = False):
    if tiled:
        # Bounded activation memory: avoids the WDDM shared-memory stall seen on this 8GB GPU
        dec = g.add("VAEDecodeTiled", PHASE_DECODE, samples=out(samples), vae=out(vae), tile_size=512,
                    overlap=64, temporal_size=64, temporal_overlap=8)
    else:
        dec = g.add("VAEDecode", PHASE_DECODE, samples=out(samples), vae=out(vae))
    save = g.add("PreviewImage", PHASE_DECODE, images=out(dec))
    return save


# ---------------------------------------------------------------- Anima

def build_anima(p: dict) -> tuple[dict, dict]:
    f = FILES[p["variant"]]
    g = G()
    unet = g.add("UNETLoader", PHASE_LOAD, unet_name=f["unet"], weight_dtype="default")
    clip = g.add("CLIPLoader", PHASE_LOAD, clip_name=f["clip"], type=f["clip_type"], device="default")
    vae = g.add("VAELoader", PHASE_LOAD, vae_name=f["vae"])
    model, clip = user_loras(g, p, out(unet), out(clip))
    pos = g.add("CLIPTextEncode", PHASE_ENCODE, clip=clip, text=p["final_prompt"])
    neg = g.add("CLIPTextEncode", PHASE_ENCODE, clip=clip, text=p.get("final_negative", ""))
    if p["mode"] == "t2i":
        latent = g.add("EmptyLatentImage", PHASE_ENCODE, width=p["width"], height=p["height"], batch_size=1)
        denoise = 1.0
    elif p["mode"] == "img2img":
        img = _fit_image(g, _load_image(g, p["images"][0]), p["width"], p["height"])
        latent = g.add("VAEEncode", PHASE_ENCODE, pixels=out(img), vae=out(vae))
        denoise = float(p["strength"])
    else:
        raise ValueError(f"Anima는 '{p['mode']}' 모드를 지원하지 않습니다.")
    ks = g.add("KSampler", PHASE_SAMPLE, model=model, positive=out(pos), negative=out(neg),
               latent_image=out(latent), seed=p["seed"], steps=p["steps"], cfg=p["cfg"],
               sampler_name=p["sampler"], scheduler=p["scheduler"], denoise=denoise)
    save = _finish(g, ks, vae, p.get("tiled_vae", False))
    return g.nodes, {"phases": g.phases, "output_node": save, "sampler_node": ks}


# ---------------------------------------------------------------- Krea 2

def build_krea2(p: dict) -> tuple[dict, dict]:
    f = FILES[p["variant"]]
    g = G()
    unet = g.add("UNETLoader", PHASE_LOAD, unet_name=f["unet"], weight_dtype="default")
    clip = g.add("CLIPLoader", PHASE_LOAD, clip_name=f["clip"], type=f["clip_type"], device="default")
    vae = g.add("VAELoader", PHASE_LOAD, vae_name=f["vae"])
    model, clip = user_loras(g, p, out(unet), out(clip))

    if p["mode"] in ("t2i", "img2img"):
        pos = g.add("CLIPTextEncode", PHASE_ENCODE, clip=clip, text=p["final_prompt"])
        neg = g.add("ConditioningZeroOut", PHASE_ENCODE, conditioning=out(pos))
        if p["mode"] == "t2i":
            latent = g.add("EmptyLatentImage", PHASE_ENCODE, width=p["width"], height=p["height"], batch_size=1)
            denoise = 1.0
        else:
            img = _fit_image(g, _load_image(g, p["images"][0]), p["width"], p["height"])
            latent = g.add("VAEEncode", PHASE_ENCODE, pixels=out(img), vae=out(vae))
            denoise = float(p["strength"])
        ks = _sampler_ksampler(g, model, pos, neg, latent, p, denoise)
        save = _finish(g, ks, vae, p.get("tiled_vae", False))
        return g.nodes, {"phases": g.phases, "output_node": save, "sampler_node": ks}

    if p["mode"] == "reference":
        # Official template: image_krea2_turbo_int8_image_style_reference.json (app LoRA, after the user LoRAs)
        lora = g.add("LoraLoaderModelOnly", PHASE_LOAD, model=model, lora_name=f["style_lora"],
                     strength_model=float(p.get("ref_strength", 1.0)))
        msf = g.add("ModelSamplingFlux", PHASE_LOAD, model=out(lora), max_shift=1.15, base_shift=0.5,
                    width=p["width"], height=p["height"])
        enc_inputs = {"clip": clip, "vae": out(vae), "prompt": p["final_prompt"]}
        for i, name in enumerate(p["images"][:3], start=1):
            enc_inputs[f"image{i}"] = out(_load_image(g, name))
        pos = g.add("TextEncodeQwenImageEditPlus", PHASE_ENCODE, **enc_inputs)
        pos = g.add("FluxKontextMultiReferenceLatentMethod", PHASE_ENCODE, conditioning=out(pos),
                    reference_latents_method="index_timestep_zero")
        neg = g.add("ConditioningZeroOut", PHASE_ENCODE, conditioning=out(pos))
        guider = g.add("CFGGuider", PHASE_SAMPLE, model=out(msf), positive=out(pos), negative=out(neg), cfg=p["cfg"])
        noise = g.add("RandomNoise", PHASE_SAMPLE, noise_seed=p["seed"])
        sampler = g.add("KSamplerSelect", PHASE_SAMPLE, sampler_name=p["sampler"])
        sigmas = g.add("BasicScheduler", PHASE_SAMPLE, model=out(msf), scheduler=p["scheduler"],
                       steps=p["steps"], denoise=1.0)
        latent = g.add("EmptyLatentImage", PHASE_ENCODE, width=p["width"], height=p["height"], batch_size=1)
        sca = g.add("SamplerCustomAdvanced", PHASE_SAMPLE, noise=out(noise), guider=out(guider),
                    sampler=out(sampler), sigmas=out(sigmas), latent_image=out(latent))
        save = _finish(g, sca, vae, p.get("tiled_vae", False))
        return g.nodes, {"phases": g.phases, "output_node": save, "sampler_node": sca}

    raise ValueError(f"Krea 2는 '{p['mode']}' 모드를 지원하지 않습니다.")


# ---------------------------------------------------------------- Qwen-Image 2.1

def build_qwen21(p: dict) -> tuple[dict, dict]:
    f = FILES[p["variant"]]
    turbo = p["variant"] == "qwen21_turbo"
    g = G()
    unet = g.add("UNETLoader", PHASE_LOAD, unet_name=f["unet"], weight_dtype="default")
    clip = g.add("CLIPLoader", PHASE_LOAD, clip_name=f["clip"], type=f["clip_type"], device="default")
    model, clip = user_loras(g, p, out(unet), out(clip))
    model = g.add("QwenImage21Cache", PHASE_LOAD, model=model, device=p.get("cache_device", "auto"),
                  dtype=p.get("cache_dtype", "default"))
    vae = g.add("VAELoader", PHASE_LOAD, vae_name=f["vae"])

    enc_inputs = {"clip": clip, "vae": out(vae), "prompt": p["final_prompt"],
                  "negative_prompt": p.get("final_negative", ""),
                  "resolution": int(p.get("ref_resolution", 1024))}
    if p["mode"] in ("edit", "reference"):
        for i, name in enumerate(p["images"][:3], start=1):
            enc_inputs[f"images.image_{i}"] = out(_load_image(g, name))
    enc = g.add("TextEncodeQwenImage21", PHASE_ENCODE, **enc_inputs)
    pos, neg = out(enc, 0), out(enc, 1)

    denoise = 1.0
    if p["mode"] == "t2i":
        latent = out(g.add("EmptyLatentImage", PHASE_ENCODE, width=p["width"], height=p["height"], batch_size=1))
    elif p["mode"] in ("edit", "reference"):
        # reference: same multi-image conditioning, but the canvas uses the requested size
        if p.get("edit_custom_size") or p["mode"] == "reference":
            latent = out(g.add("EmptyLatentImage", PHASE_ENCODE, width=p["width"], height=p["height"], batch_size=1))
        else:
            latent = out(enc, 2)  # canvas follows image_1 (official template default)
    elif p["mode"] == "img2img":
        img = _fit_image(g, _load_image(g, p["images"][0]), p["width"], p["height"])
        latent = out(g.add("VAEEncode", PHASE_ENCODE, pixels=out(img), vae=out(vae)))
        denoise = float(p["strength"])
    else:
        raise ValueError(f"Qwen-Image 2.1은 '{p['mode']}' 모드를 지원하지 않습니다.")

    if turbo:
        # Official turbo templates: fixed 8-step ManualSigmas + SamplerCustomAdvanced.
        sig = g.add("ManualSigmas", PHASE_SAMPLE, sigmas=QWEN_TURBO_SIGMAS)
        if denoise < 1.0:
            split = g.add("SplitSigmasDenoise", PHASE_SAMPLE, sigmas=out(sig), denoise=denoise)
            sig_out = out(split, 1)
        else:
            sig_out = out(sig)
        guider = g.add("CFGGuider", PHASE_SAMPLE, model=out(model), positive=pos, negative=neg, cfg=p["cfg"])
        noise = g.add("RandomNoise", PHASE_SAMPLE, noise_seed=p["seed"])
        sampler = g.add("KSamplerSelect", PHASE_SAMPLE, sampler_name=p["sampler"])
        samples = g.add("SamplerCustomAdvanced", PHASE_SAMPLE, noise=out(noise), guider=out(guider),
                        sampler=out(sampler), sigmas=sig_out, latent_image=latent)
    else:
        samples = g.add("KSampler", PHASE_SAMPLE, model=out(model), positive=pos, negative=neg,
                        latent_image=latent, seed=p["seed"], steps=p["steps"], cfg=p["cfg"],
                        sampler_name=p["sampler"], scheduler=p["scheduler"], denoise=denoise)
    save = _finish(g, samples, vae, p.get("tiled_vae", False))
    return g.nodes, {"phases": g.phases, "output_node": save, "sampler_node": samples}


# ---------------------------------------------------------------- MOSS-Image

def build_moss(p: dict) -> tuple[dict, dict]:
    """Official template image_moss_image_t2i.json (templates 0.11.97) without its optional prompt-enhancer
    (TextGenerate) and skip-layer-guidance switches, which are off by default there."""
    f = FILES[p["variant"]]
    g = G()
    unet = g.add("UNETLoader", PHASE_LOAD, unet_name=f["unet"], weight_dtype="default")
    clip = g.add("CLIPLoader", PHASE_LOAD, clip_name=f["clip"], type=f["clip_type"], device="default")
    vae = g.add("VAELoader", PHASE_LOAD, vae_name=f["vae"])
    model, clip = user_loras(g, p, out(unet), out(clip))
    pos = g.add("CLIPTextEncode", PHASE_ENCODE, clip=clip, text=p["final_prompt"])
    neg = g.add("CLIPTextEncode", PHASE_ENCODE, clip=clip, text=p.get("final_negative", ""))
    if p["mode"] == "t2i":
        latent = g.add("EmptyFlux2LatentImage", PHASE_ENCODE, width=p["width"], height=p["height"], batch_size=1)
        denoise = 1.0
    elif p["mode"] == "img2img":
        img = _fit_image(g, _load_image(g, p["images"][0]), p["width"], p["height"])
        latent = _encode(g, img, out(vae), p.get("tiled_vae", False))
        denoise = float(p["strength"])
    else:
        raise ValueError(f"MOSS-Image는 '{p['mode']}' 모드를 지원하지 않습니다.")
    ks = _sampler_ksampler(g, model, pos, neg, latent, p, denoise)
    save = _finish(g, ks, vae, p.get("tiled_vae", False))
    return g.nodes, {"phases": g.phases, "output_node": save, "sampler_node": ks}


# ---------------------------------------------------------------- Stable Diffusion (SDXL / SD1.5 / SD3.5)

def build_sd(p: dict) -> tuple[dict, dict]:
    """Checkpoint-based SD graph. p["ckpt"] = file in models/checkpoints, p["arch"] = sdxl|sd15|sd3.
    Optional p["loras"] = [{"name", "strength"}] chained with LoraLoader (model + text encoder) by user_loras."""
    g = G()
    ck = g.add("CheckpointLoaderSimple", PHASE_LOAD, ckpt_name=p["ckpt"])
    model, clip, vae = out(ck, 0), out(ck, 1), out(ck, 2)
    model, clip = user_loras(g, p, model, clip)
    pos = g.add("CLIPTextEncode", PHASE_ENCODE, clip=clip, text=p["final_prompt"])
    neg = g.add("CLIPTextEncode", PHASE_ENCODE, clip=clip, text=p.get("final_negative", ""))
    empty = "EmptySD3LatentImage" if p["arch"] == "sd3" else "EmptyLatentImage"
    composite = None
    if p["mode"] == "t2i":
        latent = out(g.add(empty, PHASE_ENCODE, width=p["width"], height=p["height"], batch_size=1))
        pos_c, neg_c, denoise = out(pos), out(neg), 1.0
    elif p["mode"] == "img2img":
        img = _fit_image(g, _load_image(g, p["images"][0]), p["width"], p["height"])
        latent = out(_encode(g, img, vae, p.get("tiled_vae", True)))
        pos_c, neg_c, denoise = out(pos), out(neg), float(p["strength"])
    elif p["mode"] == "inpaint":
        # Image and painted mask are resized to the same generation size; the mask is grown a little so
        # edges blend, DifferentialDiffusion gives a soft transition, and the result is composited back
        # onto the original so pixels outside the mask stay unchanged.
        img = _fit_image(g, _load_image(g, p["images"][0]), p["width"], p["height"])
        mimg = _fit_image(g, _load_image(g, p["mask"]), p["width"], p["height"])
        mask = g.add("ImageToMask", PHASE_ENCODE, image=out(mimg), channel="red")
        mask = g.add("ThresholdMask", PHASE_ENCODE, mask=out(mask), value=0.5)
        mask = g.add("GrowMask", PHASE_ENCODE, mask=out(mask), expand=int(p.get("mask_grow", 8)), tapered_corners=True)
        model = out(g.add("DifferentialDiffusion", PHASE_LOAD, model=model))
        # equivalent to InpaintModelConditioning(noise_mask=True) for regular (non-inpaint) checkpoints,
        # but with a tiled encode
        enc = _encode(g, img, vae, p.get("tiled_vae", True))
        latent = out(g.add("SetLatentNoiseMask", PHASE_ENCODE, samples=out(enc), mask=out(mask)))
        pos_c, neg_c = out(pos), out(neg)
        denoise = float(p["strength"])
        composite = (img, mask)
    else:
        raise ValueError(f"이 모델은 '{p['mode']}' 모드를 지원하지 않습니다.")
    ks = g.add("KSampler", PHASE_SAMPLE, model=model, positive=pos_c, negative=neg_c, latent_image=latent,
               seed=p["seed"], steps=p["steps"], cfg=p["cfg"], sampler_name=p["sampler"],
               scheduler=p["scheduler"], denoise=denoise)
    if p.get("tiled_vae"):
        dec = g.add("VAEDecodeTiled", PHASE_DECODE, samples=out(ks), vae=vae, tile_size=512, overlap=64,
                    temporal_size=64, temporal_overlap=8)
    else:
        dec = g.add("VAEDecode", PHASE_DECODE, samples=out(ks), vae=vae)
    final = dec
    if composite:
        final = g.add("ImageCompositeMasked", PHASE_DECODE, destination=out(composite[0]), source=out(dec),
                      x=0, y=0, resize_source=False, mask=out(composite[1]))
    save = g.add("PreviewImage", PHASE_DECODE, images=out(final))
    return g.nodes, {"phases": g.phases, "output_node": save, "sampler_node": ks}


# ---------------------------------------------------------------- local text generation

def build_textgen(instruction: str, user_text: str, max_length: int = 512) -> tuple[dict, dict]:
    """Prompt translation/enhancement with Krea 2's Qwen3-VL 4B text encoder (local, optional)."""
    g = G()
    clip = g.add("CLIPLoader", PHASE_LOAD, clip_name=FILES["krea2_turbo"]["clip"], type="krea2", device="default")
    # An explicit Qwen chat template is required: the krea2 tokenizer's default template is the
    # conditioning template, with which generation returns only "user" (tested on v0.40.0 and v0.43.0).
    chat = (f"<|im_start|>system\n{instruction}<|im_end|>\n"
            f"<|im_start|>user\n{user_text}<|im_end|>\n<|im_start|>assistant\n")
    gen = g.add("TextGenerate", PHASE_TEXTGEN, clip=out(clip), prompt=chat,
                max_length=max_length, sampling_mode="off", thinking=False, use_default_template=True)
    show = g.add("PreviewAny", PHASE_TEXTGEN, source=out(gen))
    return g.nodes, {"phases": g.phases, "output_node": show}


BUILDERS = {"anima": build_anima, "krea2": build_krea2, "qwen21": build_qwen21, "moss": build_moss,
            "sdxl": build_sd, "sd35": build_sd}
