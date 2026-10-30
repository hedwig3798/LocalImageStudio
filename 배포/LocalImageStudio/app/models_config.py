"""Model capability and preset definitions shown in the GUI.

Each family lists which I2I modes are supported and why others are not, plus
per-preset defaults. Values were chosen from the official model cards / Comfy-Org
templates and then adjusted by measurements on this PC (see docs/테스트_결과.md).
"""

MODES = {
    "t2i": "텍스트 → 이미지",
    "img2img": "이미지 변환 (denoise)",
    "edit": "지시문 편집",
    "reference": "참조 이미지 조건부 생성",
    "inpaint": "부분 수정 (인페인팅)",
}

SIZE_PRESETS = {
    # name: (label, width, height)  -- multiples of 32 so they suit every model
    "square": ("정사각형 1:1", 1024, 1024),
    "landscape": ("가로형 16:9", 1344, 768),
    "shorts": ("쇼츠 9:16", 768, 1344),
    "shorts_exact": ("쇼츠 9:16 (정확한 비율)", 864, 1536),
}

EXPORT_SIZES = {
    "none": ("생성 크기 그대로", 0, 0),
    "1080x1920": ("1080×1920 (쇼츠)", 1080, 1920),
    "1920x1080": ("1920×1080 (가로 FHD)", 1920, 1080),
    "1080x1080": ("1080×1080 (정사각)", 1080, 1080),
}

COMMON_SAMPLERS = ["euler", "euler_ancestral", "er_sde", "dpmpp_2m", "dpmpp_2m_sde_gpu", "dpmpp_sde", "res_multistep", "uni_pc"]
COMMON_SCHEDULERS = ["simple", "normal", "beta", "sgm_uniform", "karras"]

# How user LoRAs (models/loras, max 3) are applied, declared per family with "lora":
#   "model_clip" -> LoraLoader on diffusion model + text encoder (same strength)
#   "model_only" -> LoraLoaderModelOnly on the diffusion model
#   False/absent -> no user LoRA for that family
# "lora_archs" lists the model_scan archs whose LoRAs are offered (unknown-arch LoRAs are always offered).
# A variant may declare its own "lora_archs" (subset) to narrow that per variant (SD 3.5 Medium vs Large); the GUI
# only lists matching LoRAs and server.normalize rejects others.
# The builder in workflows.py inserts the chain right after the loaders with workflows.user_loras(), so a new
# family only needs these keys plus one user_loras() call.
LORA_APPLY = ("model_clip", "model_only")

FAMILIES = {
    "anima": {
        "label": "Anima",
        "summary": "애니메이션·일러스트 특화 2B 모델 (실사에는 부적합)",
        "multiple": 16, "min_side": 512, "max_side": 1536,
        "variants": {
            "anima_turbo": {"label": "Anima Turbo v1.1 (빠름, 8~12스텝)",
                            "precision": "BF16 (4.18GB) · TE Qwen3 0.6B BF16 · VAE Qwen-Image",
                            "negative": False, "arch": "anima"},
            "anima_aesthetic": {"label": "Anima Aesthetic v1.1 (고품질, 30스텝, 네거티브 지원)",
                                "precision": "BF16 (4.18GB) · TE Qwen3 0.6B BF16 · VAE Qwen-Image",
                                "negative": True, "arch": "anima"},
        },
        "modes": {
            "t2i": {"supported": True},
            "img2img": {"supported": True, "max_images": 1,
                        "note": "입력 이미지를 VAE로 인코딩한 뒤 변환 강도(denoise)만큼 다시 그립니다. 강도가 낮을수록 원본 구도·색이 유지됩니다."},
            "edit": {"supported": False,
                     "reason": "Anima는 텍스트→이미지 전용으로 학습된 모델이며 공식 지시문 편집 기능/가중치가 없습니다."},
            "reference": {"supported": False,
                          "reason": "Anima용 공식 인물·스타일 참조 어댑터가 없습니다. (공식 LLLite 패치는 깊이/선화 제어·인페인팅용이며 참조 생성이 아닙니다.)"},
            "inpaint": {"supported": False,
                        "reason": "Anima 인페인팅은 별도 LLLite 패치(Comfy-Org/Anima-LLLite)가 필요하며 이 앱에는 설치·연결하지 않았습니다."},
        },
        "prompt_help": "Danbooru 태그(소문자, 공백)와 영어 자연어를 섞어 쓸 수 있습니다. 자연어는 두 문장 이상 권장. 작가 태그는 @이름.",
        # Aesthetic was fine-tuned with quality tags stripped from its captions (model card: not needed in the
        # positive). Measured on this PC (2026-10-21, same seeds): "masterpiece, best quality" pulled Aesthetic
        # toward fanservice/character-sheet compositions — 'safe' gave ass-focus/pantyshot images in 3 of 4
        # seeds and 'full body' turned into split views — while no prefix gave 4/4 safe standing full body.
        "quality_prefix": {"anima_turbo": "masterpiece, best quality, score_7, ",
                           "anima_aesthetic": ""},
        # Model card: use the safety tags "in the positive and negative prompts". Added to the negative of
        # CFG>1 variants (Aesthetic) for the chosen rating tag.
        "rating_negative": {"safe": "sensitive, nsfw, explicit", "sensitive": "nsfw, explicit"},
        # User LoRAs (see LORA_APPLY below): DiT + Qwen3 text encoder (kohya Anima LoRAs train both)
        "lora": "model_clip", "lora_archs": ["anima"],
        "lora_help": "Anima LoRA는 모델(DiT)과 Qwen3 텍스트 인코더에 같은 강도로 적용됩니다.",
        "default_negative": {"anima_turbo": "",
                             "anima_aesthetic": "worst quality, low quality, blurry, jpeg artifacts, chromatic aberration, artist name"},
        "rating_tags": ["", "safe", "sensitive", "nsfw", "explicit"],
        "presets": {
            "default": {"label": "기본 (Turbo)", "variant": "anima_turbo", "steps": 10, "cfg": 1.0,
                        "sampler": "er_sde", "scheduler": "simple", "size": "square"},
            "lowmem": {"label": "저메모리 (Turbo, 768px)", "variant": "anima_turbo", "steps": 10, "cfg": 1.0,
                       "sampler": "er_sde", "scheduler": "simple", "size": "square", "width": 768, "height": 768},
            "quality": {"label": "고품질 (Aesthetic, 30스텝)", "variant": "anima_aesthetic", "steps": 30, "cfg": 4.5,
                        "sampler": "er_sde", "scheduler": "simple", "size": "square"},
        },
        "img2img_default_strength": 0.6,
    },
    "krea2": {
        "label": "Krea 2",
        "summary": "Krea 2 Turbo — 미적 품질 중심 범용 모델 (13B, INT8)",
        "multiple": 16, "min_side": 768, "max_side": 2048,
        "variants": {
            "krea2_turbo": {"label": "Krea 2 Turbo (8스텝 증류, INT8 ConvRot)",
                            "precision": "INT8 ConvRot (13.5GB) · TE Qwen3-VL 4B FP8 scaled · VAE Qwen-Image",
                            "negative": False},
        },
        "modes": {
            "t2i": {"supported": True},
            "img2img": {"supported": True, "max_images": 1,
                        "note": "입력 이미지를 VAE로 인코딩한 뒤 변환 강도(denoise)만큼 다시 그립니다. Turbo 8스텝이라 강도 0.4 미만은 변화가 거의 없습니다."},
            "edit": {"supported": False,
                     "reason": "Krea 2 공개 가중치(RAW/Turbo)는 텍스트→이미지 모델이며 공식 지시문 편집 모델이 없습니다."},
            "reference": {"supported": True, "max_images": 3,
                          "note": "공식 템플릿의 스타일 참조 LoRA(ostris/krea2_turbo_style_reference)를 사용합니다. 참조 이미지의 화풍·색감을 따라 새 이미지를 만듭니다(인물 동일성 보장은 아님)."},
            "inpaint": {"supported": False,
                        "reason": "Krea 2 공개 가중치에는 공식 인페인팅 모델·워크플로가 없습니다."},
        },
        "prompt_help": "영어 자연어 묘사가 가장 잘 동작합니다. 한국어는 '한국어→영어 번역' 옵션 사용을 권장합니다.",
        # Krea 2 LoRAs (ai-toolkit etc.) train the DiT only; the Qwen3-VL text encoder is left untouched.
        "lora": "model_only", "lora_archs": ["krea2"],
        "lora_help": "Krea 2 LoRA는 모델(DiT)에만 적용됩니다(텍스트 인코더 제외). 모든 모드(스타일 참조 포함)에서 쓸 수 있습니다. Flux.1 LoRA는 구조가 달라 쓸 수 없습니다.",
        "presets": {
            "default": {"label": "기본 (1024px, 8스텝)", "variant": "krea2_turbo", "steps": 8, "cfg": 1.0,
                        "sampler": "euler", "scheduler": "simple", "size": "square"},
            "lowmem": {"label": "저메모리 (768px)", "variant": "krea2_turbo", "steps": 8, "cfg": 1.0,
                       "sampler": "euler", "scheduler": "simple", "size": "square", "width": 768, "height": 768},
            "quality": {"label": "고품질 (1536px)", "variant": "krea2_turbo", "steps": 8, "cfg": 1.0,
                        "sampler": "euler", "scheduler": "simple", "size": "square", "width": 1536, "height": 1536},
        },
        "img2img_default_strength": 0.7,
    },
    "qwen21": {
        "label": "Qwen-Image 2.1",
        "summary": "텍스트·편집 통합 7B 모델 — 글자 표현, 지시문 편집, 다중 참조",
        "multiple": 32, "min_side": 512, "max_side": 2048,
        "variants": {
            "qwen21_turbo": {"label": "Qwen-Image 2.1 Turbo (8스텝, INT8 ConvRot)",
                             "precision": "INT8 ConvRot (7.3GB) · TE Qwen3-VL 8B INT8 ConvRot · VAE 2.1 BF16",
                             "negative": False},
            "qwen21_base": {"label": "Qwen-Image 2.1 Base (25~40스텝, INT8 ConvRot)",
                            "precision": "INT8 ConvRot (7.3GB) · TE Qwen3-VL 8B INT8 ConvRot · VAE 2.1 BF16",
                            "negative": True},
        },
        "modes": {
            "t2i": {"supported": True},
            "img2img": {"supported": True, "max_images": 1,
                        "note": "입력 이미지를 VAE로 인코딩한 뒤 변환 강도(denoise)만큼 다시 그리는 일반 변환입니다. 이 PC 실측: Turbo에서 강도 0.6~0.9는 원본 색·구도 유지 위주이고 '겨울로 바꾸기' 같은 내용 변경은 거의 반영되지 않았습니다(1.0이면 반영되지만 원본 구도가 사라짐). 색·배경 등 내용 변경은 '지시문 편집'을 쓰세요."},
            "edit": {"supported": True, "max_images": 3,
                     "note": "공식 편집 경로: 첫 번째 이미지가 편집 대상, 나머지는 참조입니다. 지시문에서 <image1>, <image2>로 지칭할 수 있습니다. 출력 크기는 첫 번째 이미지를 따릅니다."},
            "reference": {"supported": True, "max_images": 3,
                          "note": "편집과 같은 다중 참조 경로를 사용합니다. 예: '<image1>의 인물이 해변에 서 있는 사진'. 출력 크기는 아래 지정 크기를 사용합니다."},
            "inpaint": {"supported": False,
                        "reason": "Qwen-Image 2.1은 별도 마스크 입력 대신 '지시문 편집'에서 이미지 위 원·표시로 영역을 지정하는 방식을 씁니다. 이 앱의 마스크 인페인팅은 SD 계열만 지원합니다."},
        },
        "prompt_help": "영어/중국어 자연어가 기본이며 다른 언어도 이해합니다. 글자를 넣으려면 \"따옴표\" 안에 쓰세요.",
        # Qwen-Image LoRAs train the MMDiT only (the Qwen3-VL encoder also reads the edit/reference images).
        "lora": "model_only", "lora_archs": ["qwen21"],
        "lora_help": "Qwen-Image 2.1 LoRA는 모델(MMDiT)에만 적용됩니다(텍스트 인코더 제외). Qwen-Image 1.x(20B)용 LoRA는 크기가 달라 2.1에 쓸 수 없습니다.",
        "presets": {
            "default": {"label": "기본 (Turbo, 1024px)", "variant": "qwen21_turbo", "steps": 8, "cfg": 1.0,
                        "sampler": "euler", "scheduler": "simple", "size": "square"},
            "lowmem": {"label": "저메모리 (Turbo, 768px, 캐시 RAM)", "variant": "qwen21_turbo", "steps": 8, "cfg": 1.0,
                       "sampler": "euler", "scheduler": "simple", "size": "square", "width": 768, "height": 768,
                       "cache_device": "cpu"},
            "quality": {"label": "고품질 (Base, 30스텝)", "variant": "qwen21_base", "steps": 30, "cfg": 1.0,
                        "sampler": "euler", "scheduler": "simple", "size": "square"},
        },
        "img2img_default_strength": 0.6,
    },
}


FAMILIES["moss"] = {
    "label": "MOSS-Image",
    "summary": "MOSS-Image SFT (OpenMOSS, Apache-2.0) — 영어·중국어 긴 묘사와 글자 표현에 강한 범용 모델 (INT8)",
    "multiple": 16, "min_side": 512, "max_side": 2048,
    "variants": {
        "moss_sft": {"label": "MOSS-Image SFT (25스텝, INT8 ConvRot)",
                     "precision": "INT8 ConvRot (5.4GB) · TE Qwen3.5 9B W4A8 GPTQ (7.3GB) · VAE Flux.2 small",
                     "negative": True},
    },
    "modes": {
        "t2i": {"supported": True},
        "img2img": {"supported": True, "max_images": 1,
                    "note": "입력 이미지를 VAE(Flux.2)로 인코딩한 뒤 변환 강도(denoise)만큼 다시 그립니다. 이 PC 실측: 강도 0.6은 원본 구도·색 유지 위주로 화풍 변경(수채화 등)은 거의 반영되지 않았습니다. 크게 바꾸려면 0.8 이상."},
        "edit": {"supported": False,
                 "reason": "MOSS-Image 편집은 별도 편집 LoRA(moss_image_edit_lora)와 전용 인코더 노드가 필요하며 이 앱에는 설치·연결하지 않았습니다."},
        "reference": {"supported": False,
                      "reason": "MOSS-Image용 참조 생성 경로는 이 앱에 연결하지 않았습니다(편집 LoRA 필요)."},
        "inpaint": {"supported": False,
                    "reason": "MOSS-Image에는 공식 인페인팅 모델·워크플로가 없습니다."},
    },
    "prompt_help": "자세한 영어(또는 중국어) 자연어 묘사가 가장 잘 동작합니다. 글자를 넣으려면 \"따옴표\" 안에 쓰세요. 한국어는 '한국어→영어 번역' 옵션을 권장합니다.",
    # Official MOSS-Image LoRAs (edit, color/depth control; ai-toolkit) target the DiT only
    # ("layers.N.proj.q|k|v|o|ffn_*", "text_embedder.blocks.N" which is part of the DiT), never the Qwen3.5 encoder.
    "lora": "model_only", "lora_archs": ["moss"],
    "lora_help": "MOSS-Image LoRA는 모델(DiT)에만 적용됩니다(텍스트 인코더 제외). 공식 편집·컬러/깊이 제어 LoRA는 전용 노드가 필요해 일반 LoRA로는 쓸 수 없습니다.",
    # Official Comfy-Org template (image_moss_image_t2i.json, templates 0.11.97) negative
    "default_negative": "nsfw, worst quality, bad anatomy, watermark, jpeg artifact",
    "presets": {
        # Template: 25 steps, CFG 2.5, er_sde/simple, 1024x1024 (model card: 1-2MP, multiples of 16)
        "default": {"label": "기본 (1024px, 25스텝)", "variant": "moss_sft", "steps": 25, "cfg": 2.5,
                    "sampler": "er_sde", "scheduler": "simple", "size": "square"},
        "lowmem": {"label": "빠름·저메모리 (896px, 20스텝)", "variant": "moss_sft", "steps": 20, "cfg": 2.5,
                   "sampler": "er_sde", "scheduler": "simple", "size": "square", "width": 896, "height": 896},
        "quality": {"label": "고품질 (1024px, 40스텝)", "variant": "moss_sft", "steps": 40, "cfg": 2.5,
                    "sampler": "er_sde", "scheduler": "simple", "size": "square"},
    },
    "img2img_default_strength": 0.6,
}

# ---------------------------------------------------------------- Stable Diffusion families
SD_NOTE_INPAINT = ("칠한 영역만 다시 그립니다(마스크 바깥은 원본 픽셀 그대로 합성). 변환 강도 1.0 = 칠한 곳을 새로 그림, "
                   "낮을수록 원래 모양을 유지합니다.")

FAMILIES["sdxl"] = {
    "label": "Stable Diffusion (SDXL·SD1.5)",
    "summary": "SDXL Base 1.0 + models\\checkpoints 폴더의 SDXL·SD1.5 계열 체크포인트(Illustrious, Pony 등)를 자동 인식",
    "multiple": 8, "min_side": 512, "max_side": 2048,
    "dynamic_variants": True,   # filled by sd_families() from models/checkpoints
    "variants": {},
    "modes": {
        "t2i": {"supported": True},
        "img2img": {"supported": True, "max_images": 1,
                    "note": "입력 이미지를 VAE로 인코딩한 뒤 변환 강도(denoise)만큼 다시 그립니다."},
        "edit": {"supported": False, "reason": "SDXL·SD1.5에는 자연어 지시문 편집 기능이 없습니다(InstructPix2Pix 등 별도 모델 필요)."},
        "reference": {"supported": False, "reason": "참조 생성에는 IP-Adapter 같은 별도 어댑터가 필요하며 이 앱에는 설치하지 않았습니다."},
        "inpaint": {"supported": True, "max_images": 1, "note": SD_NOTE_INPAINT},
    },
    "prompt_help": "영어 태그·문장. 커뮤니티 체크포인트는 모델마다 권장 프롬프트(예: Pony의 score_9, Illustrious의 masterpiece 태그)가 다르니 배포 페이지를 확인하세요.",
    "presets": {
        "default": {"label": "기본 (25스텝)", "steps": 25, "cfg": 6.0, "sampler": "dpmpp_2m", "scheduler": "karras", "size": "square"},
        "lowmem": {"label": "저메모리 (768px)", "steps": 22, "cfg": 6.0, "sampler": "dpmpp_2m", "scheduler": "karras",
                   "size": "square", "width": 768, "height": 768},
        "quality": {"label": "고품질 (35스텝)", "steps": 35, "cfg": 6.5, "sampler": "dpmpp_2m_sde_gpu", "scheduler": "karras", "size": "square"},
    },
    "default_negative": "lowres, blurry, worst quality, low quality, bad anatomy, bad hands, watermark, text, signature",
    "img2img_default_strength": 0.6,
    "lora": "model_clip", "lora_archs": ["sdxl", "sd15"],
    "lora_help": "선택한 체크포인트와 같은 계열(SDXL에는 SDXL, SD1.5에는 SD1.5) LoRA만 보입니다. 모델과 텍스트 인코더(CLIP)에 같은 강도로 적용됩니다.",
}

FAMILIES["sd35"] = {
    "label": "Stable Diffusion 3.5",
    "summary": "Stability AI SD 3.5 Medium(2.5B)·Large(8B, FP8) — 텍스트 인코더(CLIP-L/G, T5-XXL FP8) 포함 체크포인트",
    "multiple": 16, "min_side": 512, "max_side": 1536,
    "variants": {
        "sd35_medium": {"label": "SD 3.5 Medium (기본, 2.5B)", "file": "sd3.5_medium_incl_clips_t5xxlfp8scaled.safetensors",
                        "precision": "BF16 MMDiT 2.5B + CLIP-L/G + T5-XXL FP8 scaled (11.6GB 단일 파일)", "negative": True,
                        "lora_archs": ["sd35_medium", "sd3"]},
        "sd35_large": {"label": "SD 3.5 Large FP8 (고품질, 8B, 느림)", "file": "sd3.5_large_fp8_scaled.safetensors",
                       "precision": "FP8 scaled MMDiT 8B + CLIP-L/G + T5-XXL FP8 (14.9GB 단일 파일)", "negative": True,
                       "lora_archs": ["sd35_large", "sd3"]},
    },
    "modes": {
        "t2i": {"supported": True},
        "img2img": {"supported": True, "max_images": 1,
                    "note": "입력 이미지를 VAE로 인코딩한 뒤 변환 강도(denoise)만큼 다시 그립니다."},
        "edit": {"supported": False, "reason": "SD 3.5에는 공식 지시문 편집 모델이 없습니다."},
        "reference": {"supported": False, "reason": "SD 3.5용 참조(IP-Adapter) 어댑터는 이 앱에 설치하지 않았습니다."},
        "inpaint": {"supported": True, "max_images": 1, "note": SD_NOTE_INPAINT},
    },
    "prompt_help": "영어 자연어 문장이 가장 잘 동작합니다(T5 인코더). 한국어는 '한국어→영어 번역' 옵션을 권장합니다.",
    "presets": {
        "default": {"label": "기본 (Medium, 30스텝)", "variant": "sd35_medium", "steps": 30, "cfg": 4.5,
                    "sampler": "euler", "scheduler": "sgm_uniform", "size": "square"},
        "lowmem": {"label": "저메모리 (Medium, 768px)", "variant": "sd35_medium", "steps": 28, "cfg": 4.5,
                   "sampler": "euler", "scheduler": "sgm_uniform", "size": "square", "width": 768, "height": 768},
        "quality": {"label": "고품질 (Large FP8, 28스텝)", "variant": "sd35_large", "steps": 28, "cfg": 3.5,
                    "sampler": "euler", "scheduler": "sgm_uniform", "size": "square"},
    },
    "default_negative": "blurry, low quality, distorted, deformed, watermark, text",
    "img2img_default_strength": 0.6,
    "lora": "model_clip", "lora_archs": ["sd35_medium", "sd35_large", "sd3"],
    "lora_help": "SD 3.5 LoRA는 모델(MMDiT)과 텍스트 인코더(CLIP-L/G·T5, LoRA에 들어 있을 때만)에 같은 강도로 적용됩니다. Medium용·Large용 LoRA는 서로 호환되지 않아 선택한 변형(Medium/Large)에 맞는 LoRA만 보입니다(크기를 판별하지 못한 SD3.x LoRA는 둘 다에 보임).",
}
