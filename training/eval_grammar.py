"""Evaluate a ByteProof fine-tune on grammar-correction pairs.

Reports exact-match rate, unchanged-output rate, and average Levenshtein edit
similarity (higher is closer to the reference correction).

    python eval_grammar.py \
        --model output/byteproof-qwen3-4b-lora/merged \
        --eval-file data/byteproof-gec/eval.jsonl \
        --max-examples 500
"""

from __future__ import annotations

import argparse
import json
import statistics


def load_eval(path: str, max_examples: int | None = None) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            messages = row.get("messages") or []
            if len(messages) >= 3:
                rows.append(
                    {
                        "original": messages[-2]["content"],
                        "corrected": messages[-1]["content"],
                    }
                )
            elif row.get("original") and row.get("corrected"):
                rows.append(
                    {
                        "original": row["original"],
                        "corrected": row["corrected"],
                    }
                )
            if max_examples and len(rows) >= max_examples:
                break
    return rows


def edit_similarity(a: str, b: str) -> float:
    import Levenshtein

    if a == b:
        return 1.0
    distance = Levenshtein.distance(a, b)
    max_len = max(len(a), len(b), 1)
    return max(0.0, 1.0 - distance / max_len)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="output/byteproof-qwen3-4b-lora/merged")
    parser.add_argument("--eval-file", default="data/byteproof-gec/eval.jsonl")
    parser.add_argument("--max-examples", type=int, default=500)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    args = parser.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    rows = load_eval(args.eval_file, args.max_examples)
    print(f"Loaded {len(rows)} evaluation pairs.")

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    model.eval()

    exact = 0
    unchanged = 0
    similarities: list[float] = []

    with torch.inference_mode():
        for idx, row in enumerate(rows):
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a meticulous academic proofreader. Correct grammar, "
                        "spelling, punctuation, and clarity while preserving meaning. "
                        "Return only the corrected text."
                    ),
                },
                {"role": "user", "content": row["original"]},
            ]
            inputs = tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=True,
                return_tensors="pt",
                tokenize=True,
            ).to(model.device)
            output_ids = model.generate(
                inputs,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
            )
            prediction = tokenizer.decode(
                output_ids[0][inputs.shape[1]:],
                skip_special_tokens=True,
            ).strip()

            if prediction == row["corrected"]:
                exact += 1
            if prediction == row["original"]:
                unchanged += 1
            similarities.append(edit_similarity(prediction, row["corrected"]))

            if (idx + 1) % 50 == 0:
                print(f"Evaluated {idx + 1}/{len(rows)}...")

    n = len(rows)
    print("\n--- ByteProof grammar evaluation ---")
    print(f"Exact match:           {exact / n:.1%} ({exact}/{n})")
    print(f"Unchanged output:      {unchanged / n:.1%} ({unchanged}/{n})")
    print(f"Mean edit similarity:  {statistics.mean(similarities):.3f}")


if __name__ == "__main__":
    main()
