"""Read a list of narration lines with Kokoro, one file per line.

This runs inside the Kokoro virtual environment rather than the application's,
which is why it is a separate program instead of a function.
``scripts/record_demo_videos.py`` writes a job file, runs this once per clip,
and reads back the WAV files.

Doing a whole clip in one process matters: loading the model takes a few
seconds and a clip has five or six lines, so one process per clip is the
difference between a few seconds and half a minute.

    python kokoro_narrate.py job.json

The job file is:

    {"voice": "af_heart", "speed": 1.0, "out_dir": "/tmp/x", "lines": ["...", ...]}

Kokoro's voices are named by language and accent, and the leading letter picks
the pipeline: `a` for American, `b` for British. That decides which
pronunciation dictionary is used, not just the timbre, so it matters.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2

    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    voice = job["voice"]
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    import numpy as np
    import soundfile
    from kokoro import KPipeline

    accent = voice[0] if voice[:1] in ("a", "b") else "a"
    pipeline = KPipeline(lang_code=accent)

    timings = []
    for index, text in enumerate(job["lines"]):
        spoken = text.get("spoken", text["text"]) if isinstance(text, dict) else text
        pieces = [
            audio for _, _, audio in pipeline(spoken, voice=voice, speed=job.get("speed", 1.0))
        ]
        if not pieces:
            print(f"line {index} produced no audio", file=sys.stderr)
            return 1
        audio = np.concatenate(pieces)
        path = out_dir / f"line-{index:02d}.wav"
        soundfile.write(str(path), audio, 24000)
        timings.append(
            {"index": index, "path": str(path), "seconds": round(len(audio) / 24000, 3)}
        )

    (out_dir / "timings.json").write_text(json.dumps(timings), encoding="utf-8")
    print(json.dumps(timings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
