"""Step 5 — LoRA fine-tune the router (qwen3:4b) on labeled routing examples.

  training/venv/bin/python training/train_router.py            # train + merge
  training/venv/bin/python training/train_router.py --export   # + GGUF + ollama create

Trains on training/data/router_train.jsonl (+ router_decisions the user
never overrode, once there are enough). Uses the exact production system
prompt so the fine-tuned model is a drop-in: same prompt, same JSON schema.
Test sets (router_test_real.jsonl, tests/evals) are never trained on.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Read the production prompt without importing Nexus (its deps aren't in
# the training venv).
ROUTER_SYSTEM_PROMPT = next(
    ast.literal_eval(n.value) for n in ast.parse((ROOT / "workers/llm_router.py").read_text()).body
    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ROUTER_SYSTEM_PROMPT")

BASE = "Qwen/Qwen3-4B-Instruct-2507"      # same weights family as ollama qwen3:4b
OUT = HERE / "runs" / "router"
LLAMA = Path.home() / "Dev" / "llama.cpp"
OLLAMA_NAME = "nexus-router"


def label(row: dict) -> str:
    """The JSON the router must emit — key order matches ROUTER_SCHEMA."""
    tier = "local" if row["route"] == "dispatch" else None
    return json.dumps({"route": row["route"], "tier": tier,
                       "recon_mode": bool(row["recon_mode"])})


def load_rows() -> list[dict]:
    rows = [json.loads(l) for l in open(HERE / "data" / "router_train.jsonl") if l.strip()]
    test = {json.loads(l)["text"].strip().lower()
            for l in open(HERE / "data" / "router_test_real.jsonl")}
    rows = [r for r in rows if r["text"].strip().lower() not in test]   # no leakage
    return [{"prompt": [{"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                        {"role": "user", "content": r["text"]}],
             "completion": [{"role": "assistant", "content": label(r)}]} for r in rows]


def train(epochs: float) -> Path:
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer

    ds = Dataset.from_list(load_rows()).shuffle(seed=0)
    print(f"train rows: {len(ds)}")
    cfg = SFTConfig(
        output_dir=str(OUT / "ckpt"), num_train_epochs=epochs,
        per_device_train_batch_size=4, gradient_accumulation_steps=4,
        learning_rate=2e-4, lr_scheduler_type="cosine", warmup_steps=8,
        logging_steps=10, save_strategy="no", bf16=True, report_to=[],
        completion_only_loss=True, max_length=2048, gradient_checkpointing=True,
        model_init_kwargs={"dtype": torch.bfloat16},
    )
    lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                      "gate_proj", "up_proj", "down_proj"])
    tr = SFTTrainer(model=BASE, args=cfg, train_dataset=ds, peft_config=lora)
    tr.train()
    merged = tr.model.merge_and_unload()
    dst = OUT / "merged"
    merged.save_pretrained(dst)
    tr.processing_class.save_pretrained(dst)
    print("merged →", dst)
    return dst


def export(merged: Path) -> None:
    """HF → GGUF f16 → Q4_K_M → `ollama create nexus-router`."""
    f16, q4 = OUT / "router-f16.gguf", OUT / "router-Q4_K_M.gguf"
    subprocess.run([sys.executable, str(LLAMA / "convert_hf_to_gguf.py"), str(merged),
                    "--outtype", "f16", "--outfile", str(f16)], check=True)
    subprocess.run([str(LLAMA / "build/bin/llama-quantize"), str(f16), str(q4), "Q4_K_M"],
                   check=True)
    # Reuse qwen3:4b's template/params so prompts render identically.
    mf = subprocess.run(["ollama", "show", "--modelfile", "qwen3:4b"],
                        capture_output=True, text=True, check=True).stdout
    mf = "\n".join(f"FROM {q4}" if l.startswith("FROM ") else l for l in mf.splitlines())
    (OUT / "Modelfile").write_text(mf)
    subprocess.run(["ollama", "create", OLLAMA_NAME, "-f", str(OUT / "Modelfile")], check=True)
    f16.unlink()
    print(f"ollama model ready: {OLLAMA_NAME}  (eval: venv/bin/python training/eval_router.py {OLLAMA_NAME})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--export", action="store_true")
    a = ap.parse_args()
    m = train(a.epochs)
    if a.export:
        export(m)
