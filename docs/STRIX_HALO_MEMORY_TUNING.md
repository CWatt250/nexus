# Unlocking the Full 128GB for the GPU — NIMO MME3L / Ryzen AI Max+ 395

**TL;DR: Your machine is not broken. One BIOS setting is wrong.** It's carving a
rigid **64GB VRAM block**, which starves the GPU's dynamic memory pool (GTT) and
forces big models onto the CPU. Fix the split → big models run in fast unified
memory at full speed.

---

## The machine
- **NIMO MME3L mini PC**, AMI BIOS **v3.05** (10/11/2025)
- **AMD Ryzen AI Max+ 395 / Radeon 8060S** (Strix Halo, gfx1151)
- **128GB LPDDR5-8000** (8×16GB, 256-bit, ~256 GB/s) — all one physical pool
- Kernel **6.17.0** (≥6.16.9 required for full memory access ✅)

## The diagnosis (measured)
```
VRAM carve : 64 GB   ← BIOS rigidly reserves this, GPU-only
System RAM : 62 GB   ← all that's left for the OS + GTT
GTT limit  : 124 GB  ← GPU is ALLOWED this much... but GTT draws from the 62GB
                       system-RAM side, so it can never actually reach 124GB
```
When qwen3.5 (81GB) loaded: 55GB went to VRAM, ~26GB overflowed into the 62GB
system-RAM side, **saturated it to 0GB free**, and llama.cpp shoved the rest onto
the **CPU** → **3.7 tok/s** (vs 100+ when it fits). The log proves it:
`amdgpu: SVM mapping failed, exceeds resident system memory limit`.

**The 64GB carve is the bug.** On Linux you want a *tiny* carve and let the
dynamic GTT pool use almost all 128GB. (Confirmed by Framework/AMD's Mario
Limonciello: "set it as low as possible and tune the TTM parameters instead.")

---

## THE FIX

### Part A — BIOS (you do this at reboot; I can't change BIOS remotely)

1. Reboot, press **Del** (or **F2**) repeatedly at the NIMO splash to enter BIOS.
2. Go to **Advanced**. Find the integrated-GPU memory setting. On this AMI BIOS
   it's under one of:
   - **Advanced → Integrated Graphics → UMA Frame Buffer Size**, or
   - **Advanced → AMD CBS → NBIO Common Options → GFX Configuration → UMA Frame Buffer Size**, or
   - **Advanced → AMD PBS → iGPU Configuration**
   Look for **"UMA Frame Buffer Size"** (may be called *Dedicated Graphics
   Memory* / *iGPU Memory*).
3. Set it to **512M** (the minimum). **Not** "Auto" — Auto scales badly for LLMs.
4. *(Optional, ~6% faster memory reads)* Find **IOMMU** and set to **Disabled**.
5. **Save & Exit** (F10).

After this, Linux will see **~127GB as system RAM**, and the GPU's GTT pool can
finally use almost all of it.

### Part B — Linux kernel (ALREADY DONE on your box — just verify)

Your GRUB already has `ttm.pages_limit=32505856` (=124GB GTT). That's good, but
after Part A I'd dial it to ~115GB to leave the OS headroom. Current cmdline:
```
amdgpu.gttsize=126976 ttm.pages_limit=32505856 ttm.page_pool_size=32505856
```
Recommended after the BIOS change (leaves ~12GB for the OS):
```
amdgpu.gttsize=117760 ttm.pages_limit=30146560 ttm.page_pool_size=30146560
```
(GTT sizes: 117760MB≈115GB · 122880MB≈120GB · edit `/etc/default/grub`, then
`sudo update-grub` and reboot. Nexus can prep this file for you.)

---

## Expected result
- System RAM visible: **62GB → ~127GB**
- An **81GB model loads entirely into fast unified memory** — no CPU fallback
- qwen3.5-122B should jump from **3.7 tok/s → ~15–30 tok/s** (MoE, all-GPU)
- You can run **70B–120B models** the way this machine was sold to

## Verify after reboot
```bash
free -g | awk 'NR==2{print "system RAM:", $2"GB"}'      # expect ~127
cat /sys/class/drm/card*/device/mem_info_vram_total     # expect ~512MB, tiny
# then re-run the model benchmark — watch it stay on GPU at full speed
```

## Sources
- Framework community — BIOS VRAM config (Mario Limonciello / AMD)
- Gygeek/Framework-strix-halo-llm-setup (BIOS + kernel + 70B on one APU)
- hogeheer499-commits/strix-halo-guide (Vulkan/RADV, 100 t/s 30B, 120B GGUF)
- Jeff Geerling — increasing VRAM allocation on AMD AI APUs under Linux
