# Local image generation (stable-diffusion.cpp on Vulkan)

Nexus generates images locally on the Radeon 8060S iGPU (gfx1151) via the
prebuilt **stable-diffusion.cpp Vulkan** binary — $0, offline, ~10s for a
512×512 SD1.5 image. No ROCm/torch/CUDA involved (matches the Vulkan
inference stack).

The binary and model are large and **gitignored** (`models/` is ignored),
so re-provision them on a fresh checkout with the steps below.

## Assets (re-download on a fresh machine)

```bash
cd ~/AI_Agent/models/sdcpp        # mkdir -p if missing

# 1. Prebuilt sd.cpp Vulkan binary for Ubuntu 24.04 x86_64 (~43 MB)
curl -sL -o sdcpp-vulkan.zip \
  https://github.com/leejet/stable-diffusion.cpp/releases/download/master-737-3b6c9ca/sd-master-3b6c9ca-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip
unzip -o sdcpp-vulkan.zip && rm sdcpp-vulkan.zip   # → sd-cli, sd-server, lib*.so

# 2. Diffusion model — SD1.5 fp16 single-file (~2.0 GB)
mkdir -p models
curl -sL -o models/sd15.safetensors \
  https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly-fp16.safetensors
```

## Smoke test

```bash
cd ~/AI_Agent/models/sdcpp
LD_LIBRARY_PATH=. ./sd-cli -m models/sd15.safetensors \
  -p "a red apple on a wooden table, photorealistic" \
  --steps 20 -W 512 -H 512 -o /tmp/test.png
# Expect: "ggml_vulkan: Found 1 Vulkan devices: AMD Radeon Graphics (RADV GFX1151)"
# and /tmp/test.png written in ~10s.
```

## Models

The tool (`tools/image_gen_tool.py`) supports these local models via `model=`:

| model | quality | size | speed | notes |
|-------|---------|------|-------|-------|
| `flux` (**default**) | best all-round — real in-image **text** | 1024 | ~40s | FLUX.1-schnell Q4, 12B |
| `qwenturbo` | Qwen-2.1 adherence + clean text, 4 steps | 1024 | ~30s cold / ~18s warm | Viggle Turbo transformer on ComfyUI |
| `qwen21` | **quality tier** — best prompt adherence on busy scenes | 1024 | ~120s | Qwen-Image-2.1 on ComfyUI, *not* sd.cpp |
| `sdxl` | detailed | 1024 | ~21s | SDXL-Turbo |
| `sd15` | soft/cute, fastest | 512 | ~10s | SD1.5 |

### The `qwen21` quality tier

`tools/comfy_qwen.py` drives **ComfyUI** (`~/Dev/ComfyUI`) rather than sd.cpp.
Measured head-to-head on a 6-element flat-lay prompt: FLUX dropped three of the
six specified items, Qwen-2.1 got all six. On single subjects the two tie, so
the extra ~80s is only worth it when the prompt names several things that must
all appear, or a specific layout.

ComfyUI is a server holding ~30-42 GB resident, so it is **started on demand
and torn down** when the request finishes — zero standing memory cost, which
matters because the brain already holds ~21 GB. If the server is already up
(you ran `launch.sh` yourself) it is reused and left running.

### The `qwenturbo` tier

Same ComfyUI path, with the base UNET swapped for Viggle's 4-step
DMD-distilled Qwen-Image-2.1 transformer (no CFG). Measured 2026-09-23 on a
dusk-desk prompt: 4 steps at ~2.7 s/step, 18s per image with ComfyUI warm,
30s from a cold start; text rendered cleanly on 2/3 seeds. Slightly less
fine detail than 25-step `qwen21`. **License: Qwen RESEARCH — non-commercial
only**; fine for personal Nexus, not for BrainBox without a commercial license.

```bash
cd ~/Dev/ComfyUI/models/diffusion_models
curl -L -o qwen_image_2.1_viggle_turbo_bf16.safetensors \
  https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo/resolve/main/transformer/diffusion_pytorch_model.safetensors
```

The file is diffusers-keyed (split `gate_layer`/`proj` MLP); ComfyUI loads it
as-is — no conversion needed.

`launch.sh` **must** pass `--disable-mmap` or weight loading never finishes on
this box — mmap-backed H2D copies run at 0.19 GB/s vs 17 GB/s resident. See
`memory/lessons.md` 2026-09-20.

### Provision FLUX.1-schnell (the default — Apache-2.0, free)

```bash
cd ~/AI_Agent/models/sdcpp && mkdir -p flux && cd flux
curl -sL -o flux1-schnell-Q4_K_S.gguf \
  https://huggingface.co/city96/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-Q4_K_S.gguf   # 6.8 GB
curl -sL -o t5xxl_fp8.safetensors \
  https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors  # 4.9 GB
curl -sL -o clip_l.safetensors \
  https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors   # 246 MB
curl -sL -o ae.safetensors \
  https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main/ae.safetensors        # 335 MB (ungated VAE mirror)
```
For higher quality swap the model file for `flux1-schnell-Q8_0.gguf` (~12.7 GB)
and update the path in `MODELS["flux"]` in `tools/image_gen_tool.py`.

### Provision SDXL-Turbo (optional)
```bash
curl -sL -o ~/AI_Agent/models/sdcpp/models/sdxl-turbo.safetensors \
  https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors
```

### Qwen-Image-2512 on sd.cpp — REMOVED 2026-09-20

The old `model="qwen"` (Qwen-Image-2512 Q4, 20B, ~17.4 GB, ~340 s/image) was
deleted. `qwen21` above is the same family, newer, and 4-5x faster, so 2512 was
pure disk cost. Its `MODELS` entry is gone — nothing silently falls back.

Note: Qwen-Image-**3.0** (July 2026) is API-only — no weights — so
Qwen-Image-2.1 via ComfyUI is the newest local option.

## Usage

- **Tool:** `generate_image(prompt, model="flux"|"qwenturbo"|"qwen21"|"sdxl"|"sd15", ...)`
  (heavy agent) → saves to `output/images/`.
- **Telegram:** `/image <prompt>` (FLUX) or `/image sd15 <prompt>` (fast) or
  `/image qwenturbo <prompt>` (~30s, clean text) or
  `/image qwen21 <prompt>` (~2 min, best on busy multi-element scenes).
