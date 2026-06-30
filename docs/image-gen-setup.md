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

## Usage

- **Tool:** `tools/image_gen_tool.generate_image(prompt, size, ...)` (heavy
  agent) → saves to `output/images/`.
- **Telegram:** `/image <prompt>` → generates and sends the photo.

## Higher quality (optional)

Swap the model for **SDXL-Turbo** (~6.9 GB, 1024px, 4 steps) and point
`SD_MODEL` in `tools/image_gen_tool.py` at it:

```bash
curl -sL -o models/sdxl-turbo.safetensors \
  https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors
```
