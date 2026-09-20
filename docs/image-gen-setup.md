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
| `qwen21` | **quality tier** — best prompt adherence on busy scenes | 1024 | ~120s | Qwen-Image-2.1 on ComfyUI, *not* sd.cpp |
| `sdxl` | detailed | 1024 | ~21s | SDXL-Turbo |
| `sd15` | soft/cute, fastest | 512 | ~10s | SD1.5 |
| `qwen` | superseded by `qwen21` (4-5x slower, older) | 1024 | ~340s | Qwen-Image-2512 Q4, 20B |

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

### Provision Qwen-Image-2512 (optional, best text/layout quality)
20B model — ~17.4 GB of assets, ~6 min per 1024px image (20 steps @ ~16 s/it).
Installed 2026-07-22, verified working (chalkboard-sign text test passed).
```bash
mkdir -p ~/AI_Agent/models/sdcpp/qwen && cd ~/AI_Agent/models/sdcpp/qwen
wget -c https://huggingface.co/unsloth/Qwen-Image-2512-GGUF/resolve/main/qwen-image-2512-Q4_K_M.gguf                     # 12.6 GB
wget -c https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/Qwen2.5-VL-7B-Instruct-UD-Q4_K_XL.gguf  # 4.6 GB
wget -c https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors     # 243 MB
```
Settings live in `MODELS["qwen"]`: euler / 20 steps / cfg 2.5 / flow-shift 3,
900 s timeout. Note: Qwen-Image-**3.0** (July 2026) is API-only — no weights —
which is why 2512 is the newest local option.

## Usage

- **Tool:** `generate_image(prompt, model="flux"|"qwen21"|"sdxl"|"sd15", ...)`
  (heavy agent) → saves to `output/images/`.
- **Telegram:** `/image <prompt>` (FLUX) or `/image sd15 <prompt>` (fast) or
  `/image qwen21 <prompt>` (~2 min, best on busy multi-element scenes).
