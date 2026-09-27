"""Step 6 — teach the brain Colton's preferences from 👍/👎 (KTO + LoRA).

  training/venv/bin/python training/export_feedback.py      # ratings → data/brain_kto.jsonl
  training/venv/bin/python training/train_brain_kto.py      # needs --min-rows rated turns
  training/venv/bin/python training/train_brain_kto.py --export   # + GGUF + ollama create

KTO learns from unpaired thumbs up/down (no need for two answers to the same
question). LoRA keeps the change small; a low learning rate + 1 epoch keeps
it from drifting — the goal is style/judgement, not new knowledge.

Memory: Ornith-1.5 is ~72 GB in bf16. Training needs the resident brain
unloaded (`ollama stop`) and ComfyUI down. Pipeline validated end-to-end on
Qwen/Qwen3.5-0.8B (same hybrid linear-attention family): --base Qwen/Qwen3.5-0.8B.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LLAMA = Path.home() / "Dev" / "llama.cpp"
BRAIN = "ornith-ai/Ornith-1.5-35B-A3B"
MIN_ROWS = 200          # below this, KTO mostly memorizes noise


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path) if l.strip()]


def train(base: str, data: Path, out: Path, epochs: float) -> Path:
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import KTOConfig, KTOTrainer

    rows = load(data)
    up = sum(r["label"] for r in rows)
    print(f"{len(rows)} rows ({up} 👍 / {len(rows) - up} 👎)")
    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(base, dtype=torch.bfloat16)
    # KTO wants the desirable/undesirable ratio near 1; weight the rarer side up.
    down = max(len(rows) - up, 1)
    cfg = KTOConfig(
        output_dir=str(out / "ckpt"), num_train_epochs=epochs,
        per_device_train_batch_size=2, gradient_accumulation_steps=8,
        learning_rate=5e-6 if "35B" in base else 2e-5, beta=0.1,
        desirable_weight=min(max(down / max(up, 1), 1.0), 4.0),
        undesirable_weight=min(max(up / down, 1.0), 4.0),
        max_length=2048, logging_steps=5, save_strategy="no", bf16=True,
        report_to=[], gradient_checkpointing=True,
    )
    lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM",
                      target_modules="all-linear",
                      exclude_modules=r".*(visual|vision|mlp\.gate|router|shared_expert_gate).*")
    tr = KTOTrainer(model=model, args=cfg, train_dataset=Dataset.from_list(rows),
                    processing_class=tok, peft_config=lora)
    tr.train()
    tr.model.save_pretrained(out / "adapter")
    merged = tr.model.merge_and_unload()
    merged.save_pretrained(out / "merged")
    tok.save_pretrained(out / "merged")
    print("merged →", out / "merged")
    return out / "merged"


def export(merged: Path, out: Path, name: str, template_from: str) -> None:
    f16, q4 = out / "brain-f16.gguf", out / "brain-Q4_K_M.gguf"
    subprocess.run([sys.executable, str(LLAMA / "convert_hf_to_gguf.py"), str(merged),
                    "--outtype", "bf16", "--outfile", str(f16)], check=True)
    subprocess.run([str(LLAMA / "build/bin/llama-quantize"), str(f16), str(q4), "Q4_K_M"],
                   check=True)
    mf = subprocess.run(["ollama", "show", "--modelfile", template_from],
                        capture_output=True, text=True, check=True).stdout
    mf = "\n".join(f"FROM {q4}" if l.startswith("FROM ") else l for l in mf.splitlines())
    (out / "Modelfile").write_text(mf)
    subprocess.run(["ollama", "create", name, "-f", str(out / "Modelfile")], check=True)
    f16.unlink()
    print(f"ollama model ready: {name}. A/B it before touching models.json.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BRAIN)
    ap.add_argument("--data", type=Path, default=HERE / "data" / "brain_kto.jsonl")
    ap.add_argument("--out", type=Path, default=HERE / "runs" / "brain")
    ap.add_argument("--epochs", type=float, default=1)
    ap.add_argument("--min-rows", type=int, default=MIN_ROWS)
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--name", default="nexus-brain")
    ap.add_argument("--template-from", default="hf.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF:Q4_K_M")
    a = ap.parse_args()
    n = len(load(a.data)) if a.data.exists() else 0
    if n < a.min_rows:
        sys.exit(f"only {n} rated turns in {a.data} — need {a.min_rows}. "
                 f"Keep reacting 👍/👎 in Telegram; run export_feedback.py again later.")
    m = train(a.base, a.data, a.out, a.epochs)
    if a.export:
        export(m, a.out, a.name, a.template_from)
