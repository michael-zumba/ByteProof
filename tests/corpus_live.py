"""Live accuracy corpus for the preview provider.

Run manually with a configured provider:
    ./venv/bin/python tests/corpus_live.py

Each item is (text, must_fix) where must_fix maps an erroneous substring to a
corrected substring. Accuracy means: applying the preview edits removes the
error, so the corrected text no longer contains the bad form and does contain
the good form. Edit granularity (single word vs phrase) is intentionally not
asserted, because phrase-level fixes are equally valid corrections.
"""

import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

CORPUS: list[tuple[str, list[tuple[str, str]]]] = [
    ("teh cat sat on teh mat", [("teh mat", "the mat")]),
    ("He go to school every day", [("He go", "He goes")]),
    ("The data is important for are analysis", [("are analysis", "our analysis")]),
    (
        "This results are promising and it show potential",
        [("This results", "These results"), ("it show", "show")],
    ),
    (
        "I have recieved your email and will responde soon",
        [("recieved", "received"), ("responde", "respond")],
    ),
]


def apply_edits(text: str, edits) -> str:
    from src.live_preview import map_edits_to_ranges

    spans = map_edits_to_ranges(text, edits)
    parts: list[str] = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor : span.start])
        parts.append(span.after)
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def main() -> int:
    from src import logic
    from src.settings import load_runtime_settings

    settings = load_runtime_settings()
    settings["live_preview"] = {
        "enabled": True,
        "delay_ms": 900,
        "max_chars": 1500,
        "use_local_model": False,
    }
    target = {"bundle_id": "com.apple.mail", "name": "Mail", "pid": 0}
    failures = 0
    for text, must_fix in CORPUS:
        status, edits, meta = logic.preview_edits_once(
            settings, target, text, "", ""
        )
        corrected = apply_edits(text, edits)
        problems = []
        for bad, good in must_fix:
            bad_pattern = re.compile(r"\b" + re.escape(bad) + r"\b")
            good_pattern = re.compile(r"\b" + re.escape(good) + r"\b")
            if bad_pattern.search(corrected):
                problems.append(f"{bad!r} still present")
            if not good_pattern.search(corrected):
                problems.append(f"{good!r} missing")
        print(f"[{meta.get('provider')}] {text!r} -> {corrected!r}")
        print(
            f"    edits={json.dumps([e.__dict__ for e in edits], ensure_ascii=False)}"
        )
        if problems:
            failures += 1
            print(f"    FAIL: {problems}")
        else:
            print("    OK")
    print(f"CORPUS_RESULT failures={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
