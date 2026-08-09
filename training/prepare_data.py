"""Build ByteProof's grammar-correction training/eval datasets.

Sources (all public research datasets):
  - BEA-2019 / W&I+LOCNESS (learner essays, sentence-level pairs)
  - JFLEG (fluency-oriented GEC benchmark)
  - CoNLL-2014 (classic GEC test benchmark; use for evaluation only)

The script writes ChatML instruction pairs in the same format the ByteProof
app uses at inference time. A small amount of synthetic data can also be
generated with a strong model (e.g. DeepSeek V4 Pro) by writing JSONL lines
{"original": "...", "corrected": "..."} into `synthetic/`.

Usage:
    python prepare_data.py \
        --out-dir data/byteproof-gec \
        --max-train 50000 \
        --max-eval 2000

Datasets are cached by Hugging Face `datasets` on first run.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

SYSTEM_PROMPT = (
    "You are a meticulous academic proofreader. Correct grammar, spelling, "
    "punctuation, and clarity while preserving meaning, citations, numbers, "
    "formula notation, and paragraph structure. Return only the corrected text."
)


def chatml_pair(original: str, corrected: str) -> dict:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": original.strip()},
            {"role": "assistant", "content": corrected.strip()},
        ]
    }


def load_local_gec_folder(folder: str) -> list[tuple[str, str]]:
    """Load the BEA-2019 shared task layout: file.origin / file.correct."""
    pairs: list[tuple[str, str]] = []
    origin_path = Path(folder)
    for origin_file in sorted(origin_path.glob("*.origin")):
        correct_file = origin_file.with_suffix(".correct")
        if not correct_file.exists():
            continue
        with origin_file.open(encoding="utf-8") as f:
            originals = f.read().splitlines()
        with correct_file.open(encoding="utf-8") as f:
            corrects = f.read().splitlines()
        for orig, corr in zip(originals, corrects, strict=False):
            if orig.strip() and corr.strip():
                pairs.append((orig, corr))
    return pairs


def load_jfleg() -> list[tuple[str, str]]:
    from datasets import load_dataset

    dataset = load_dataset("jhu-clsp/jfleg", split="train")
    pairs: list[tuple[str, str]] = []
    for row in dataset:
        original = row.get("sentence", row.get("original", "")).strip()
        corrections = row.get("corrections") or row.get("correct") or []
        if isinstance(corrections, str):
            corrections = [corrections]
        for corr in corrections:
            if original and corr and corr.strip():
                pairs.append((original, corr.strip()))
    return pairs


def load_bea2019(split: str = "train") -> list[tuple[str, str]]:
    from datasets import load_dataset

    dataset = load_dataset("bea2019st/wi_locness", split=split)
    pairs: list[tuple[str, str]] = []
    for row in dataset:
        original = (row.get("original") or row.get("src") or "").strip()
        corrected = (row.get("corrected") or row.get("trg") or "").strip()
        if original and corrected:
            pairs.append((original, corrected))
    return pairs


def load_synthetic(folder: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for path in sorted(Path(folder).glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("original") and row.get("corrected"):
                    pairs.append((row["original"], row["corrected"]))
    return pairs


def dedupe(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for orig, corr in pairs:
        key = (orig.lower(), corr.lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append((orig, corr))
    return unique


def write_jsonl(pairs: list[tuple[str, str]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for orig, corr in pairs:
            f.write(json.dumps(chatml_pair(orig, corr), ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="data/byteproof-gec")
    parser.add_argument("--max-train", type=int, default=50_000)
    parser.add_argument("--max-eval", type=int, default=2_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bea-folder", default="", help="Local BEA-2019 .origin/.correct folder")
    parser.add_argument("--synthetic-folder", default="synthetic")
    args = parser.parse_args()

    random.seed(args.seed)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    train_pairs: list[tuple[str, str]] = []
    eval_pairs: list[tuple[str, str]] = []

    if args.bea_folder and os.path.isdir(args.bea_folder):
        train_pairs += load_local_gec_folder(args.bea_folder)
    else:
        print("Loading BEA-2019 train split from Hugging Face...")
        train_pairs += load_bea2019("train")

    print("Loading JFLEG train split from Hugging Face...")
    train_pairs += load_jfleg()

    synthetic = load_synthetic(args.synthetic_folder)
    if synthetic:
        print(f"Adding {len(synthetic)} synthetic pairs.")
        train_pairs += synthetic

    train_pairs = dedupe(train_pairs)
    random.shuffle(train_pairs)
    if len(train_pairs) > args.max_train:
        train_pairs = train_pairs[: args.max_train]

    print("Loading BEA-2019 dev split for evaluation...")
    eval_pairs = dedupe(load_bea2019("dev") + load_synthetic(args.synthetic_folder))
    random.shuffle(eval_pairs)
    if len(eval_pairs) > args.max_eval:
        eval_pairs = eval_pairs[: args.max_eval]

    train_path = out_dir / "train.jsonl"
    eval_path = out_dir / "eval.jsonl"
    write_jsonl(train_pairs, train_path)
    write_jsonl(eval_pairs, eval_path)

    print(f"Wrote {len(train_pairs)} training pairs -> {train_path}")
    print(f"Wrote {len(eval_pairs)} evaluation pairs -> {eval_path}")


if __name__ == "__main__":
    main()
