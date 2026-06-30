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

The tool (`tools/image_gen_tool.py`) supports three local models via `model=`:

| model | quality | size | speed | notes |
|-------|---------|------|-------|-------|
| `flux` (**default**) | best — real in-image **text**, strong prompt adherence | 1024 | ~37s | FLUX.1-schnell Q4, 12B |
| `sdxl` | detailed | 1024 | ~21s | SDXL-Turbo |
| `sd15` | soft/cute, fastest | 512 | ~10s | SD1.5 |

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

## Usage

- **Tool:** `generate_image(prompt, model="flux"|"sdxl"|"sd15", ...)` (heavy
  agent) → saves to `output/images/`.
- **Telegram:** `/image <prompt>` (FLUX) or `/image sd15 <prompt>` (fast).
