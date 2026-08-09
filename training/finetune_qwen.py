"""LoRA fine-tuning for the ByteProof local proofreading model.

Train Qwen3-4B (or 8B) on grammar-correction pairs in ChatML format:

    python finetune_qwen.py \
        --train-file data/byteproof-gec/train.jsonl \
        --eval-file data/byteproof-gec/eval.jsonl \
        --base-model Qwen/Qwen3-4B \
        --output-dir output/byteproof-qwen3-4b-lora \
        --epochs 2 \
        --batch-size 4

The output LoRA can be merged and converted to GGUF for the ByteProof app:

    1. Merge:   python finetune_qwen.py --merge-only ...
    2. Convert: python llama.cpp/convert_hf_to_gguf.py <merged> --outtype f16
    3. Quant:   llama.cpp/llama-quantize <f16.gguf> Q4_K_M.gguf q4_k_m
    4. Publish the GGUF and add it to byteproof-models.json (or your CDN).

Requirements: a GPU with ~12-16 GB VRAM for 4B LoRA, ~24 GB for 8B.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def load_chatml(path: str, max_examples: int | None = None) -> list[dict]:
    examples: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("messages"):
                examples.append(row)
            elif row.get("original") and row.get("corrected"):
                examples.append(
                    {
                        "messages": [
                            {"role": "user", "content": row["original"]},
                            {"role": "assistant", "content": row["corrected"]},
                        ]
                    }
                )
            if max_examples and len(examples) >= max_examples:
                break
    return examples


def formatting_func(examples: list[dict]) -> list[str]:
    texts: list[str] = []
    for row in examples:
        messages = row["messages"]
        parts: list[str] = []
        for msg in messages:
            role = msg["role"]
            if role == "system":
                parts.append(f"<|im_start|>system\n{msg['content']}<|im_end|>\n")
            elif role == "user":
                parts.append(f"<|im_start|>user\n{msg['content']}<|im_end|>\n")
            elif role == "assistant":
                parts.append(f"<|im_start|>assistant\n{msg['content']}<|im_end|>\n")
        parts.append("<|im_start|>assistant\n")  # SFTTrainer predicts the assistant turn
        texts.append("".join(parts))
    return texts


def find_linear_modules(model) -> list[str]:
    """Find all linear layer names (works for Qwen3/Phi/Gemma)."""
    import torch

    modules: set[str] = set()
    for name, module in model.named_modules():
        if isinstance(module, torch.nn.Linear):
            parts = name.split(".")
            modules.add(parts[-1] if parts else name)
    return list(modules)


def train(args: argparse.Namespace) -> None:
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        TrainingArguments,
    )
    from trl import SFTTrainer

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    train_examples = load_chatml(args.train_file, args.max_train)
    eval_examples = load_chatml(args.eval_file, args.max_eval)
    print(f"Training examples: {len(train_examples)}  Eval examples: {len(eval_examples)}")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quantization_config = None
    if args.quantize:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16 if args.bf16 else torch.float16,
            bnb_4bit_quant_type="nf4",
        )

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16 if args.bf16 else torch.float16,
        quantization_config=quantization_config,
        device_map="auto",
        trust_remote_code=True,
    )
    if quantization_config is not None:
        model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=args.target_modules or find_linear_modules(model),
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    training_args = TrainingArguments(
        output_dir=args.output_dir,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        bf16=args.bf16,
        fp16=not args.bf16,
        logging_steps=25,
        evaluation_strategy="steps" if eval_examples else "no",
        eval_steps=200,
        save_steps=500,
        save_total_limit=2,
        load_best_model_at_end=bool(eval_examples),
        report_to="none",
        gradient_checkpointing=args.gradient_checkpointing,
        optim="adamw_torch",
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=__import__("datasets").Dataset.from_list(train_examples),
        eval_dataset=__import__("datasets").Dataset.from_list(eval_examples) if eval_examples else None,
        tokenizer=tokenizer,
        formatting_func=formatting_func,
        max_seq_length=args.max_seq_length,
    )
    trainer.train()
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print(f"Saved LoRA to {args.output_dir}")


def merge(args: argparse.Namespace) -> None:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    merged_dir = args.merged_dir or os.path.join(args.output_dir, "merged")
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16 if args.bf16 else torch.float16,
        device_map="cpu",
        trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(model, args.output_dir)
    model = model.merge_and_unload()
    model.save_pretrained(merged_dir, safe_serialization=True)
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    tokenizer.save_pretrained(merged_dir)
    print(f"Merged model saved to {merged_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-file", default="data/byteproof-gec/train.jsonl")
    parser.add_argument("--eval-file", default="data/byteproof-gec/eval.jsonl")
    parser.add_argument("--base-model", default="Qwen/Qwen3-4B")
    parser.add_argument("--output-dir", default="output/byteproof-qwen3-4b-lora")
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--max-train", type=int, default=50_000)
    parser.add_argument("--max-eval", type=int, default=1_000)
    parser.add_argument("--quantize", action="store_true", help="4-bit QLoRA")
    parser.add_argument("--bf16", action="store_true", default=True)
    parser.add_argument("--gradient-checkpointing", action="store_true", default=True)
    parser.add_argument("--target-modules", nargs="*", default=None)
    parser.add_argument("--merge-only", action="store_true")
    parser.add_argument("--merged-dir", default="")
    args = parser.parse_args()

    if args.merge_only:
        merge(args)
    else:
        train(args)


if __name__ == "__main__":
    main()
