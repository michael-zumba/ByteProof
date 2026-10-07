#!/usr/bin/env python3
"""Film ByteProof proofreading a real Word document, on the screen it lives on.

The composited set in ``demos`` is built offscreen from the app's own widgets.
These films are the other kind: the screen is recorded while ByteProof edits
``manuscript_immersive_tech_disclosure.docx`` in Microsoft Word, so every
tracked change a viewer sees is a Word revision, and every wait is the wait
the model actually took.

What is real, and what is arranged:

* The picture is the applications working. The keystrokes are real keystrokes,
  the settings window is the app's own dialog, the edits are applied by the
  app's own apply path, and the revisions in the document are tracked changes.
* What is arranged is the stage: a copy of the manuscript is opened for each
  take, the windows are put where the frame expects them, and the app is left
  on a known set of settings before the camera rolls. The original document is
  never opened, so it cannot be edited by a film.
* The narration is written against each screen, and read before the camera
  rolls, so the picture waits for the sentence. The model's own speed is the
  one part that cannot be scripted, which is why the waits are covered by
  spoken lines rather than by a cut.

    python scripts/record_word_demo.py --plan scripts/word_demo_clips.py --list
    python scripts/record_word_demo.py --plan scripts/word_demo_clips.py \
        --only 01-academic-journal --out /tmp/try
    python scripts/record_word_demo.py --plan scripts/word_demo_clips.py \
        --out demos/word

One clip before the set. The audit sheet (``<name>-audit.jpg``) is the only
cheap way to catch narration describing a screen the viewer is not looking at.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))

SKILL_SCRIPTS = Path.home() / ".codex/skills/bytemind-demo-video/scripts"
if not (SKILL_SCRIPTS / "record_videos.py").exists():
    raise SystemExit(
        "the bytemind-demo-video skill is not installed at "
        f"{SKILL_SCRIPTS}; these films are written by its engine"
    )
sys.path.insert(0, str(SKILL_SCRIPTS))

import record_native_videos as native
import record_videos as engine
from word_demo_driver import WordDemo

WIDTH, HEIGHT = engine.WIDTH, engine.HEIGHT
log = engine.log

# The settings every film starts from. Each clip changes one of them on
# camera, so a film's subject is the only thing that differs between takes.
BASE_SETTINGS = {
    "temperature": 0.2,
    "style": "Precise (Minimal Changes)",
    "spelling": "UK/AU/NZ",
    "context": "General Editing",
    "comment_type": "None",
    "track_changes": True,
    "auto_apply": True,
    "keep_on_top": False,
}
# Live Check stays on for the set, so the window looks the way it does on a
# machine in use. Word is the one app it is told to leave alone: its card would
# otherwise draw over the paragraph these films are about.
LIVE_APP_RULES = {"com.microsoft.Word": False}
SETTINGS_PATH = Path.home() / "Library/Application Support/ByteMind/ByteProof/settings.json"


def reset_base_settings() -> None:
    """Put the app back on the settings every film starts from."""
    data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    general = data.setdefault("general", {})
    for key, value in BASE_SETTINGS.items():
        general[key] = value
    live = data.setdefault("live_preview", {})
    live["enabled"] = True
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def word_rule(marker: bool | None = None) -> bool:
    """Read, and optionally write, Live Check's rule for Word."""
    data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    rules = data.setdefault("live_preview", {}).setdefault("app_rules", {})
    current = bool(rules.get("com.microsoft.Word", True))
    if marker is None:
        return current
    rules["com.microsoft.Word"] = marker
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return current


class LiveCapture(native.Capture):
    """The screen recorder, with an honest clock.

    ``Capture.start`` waits until the file has grown past 4 KB before it notes
    when the recording began. On a still screen that wait is long, because a
    still screen compresses to almost nothing: measured on this machine it took
    6.3 seconds. The note is then 6.3 seconds late, the crop starts that far
    into the recording, and every frame arrives that far behind the narration
    that was written for it.

    The fix is to give the encoder something to chew on while it starts: the
    pointer is wiggled in the pre-roll, which is cut away before the title
    card, so the file grows within a few frames and the clock stays true.
    """

    def start(self) -> None:
        import threading

        stop = threading.Event()

        def wiggle() -> None:
            import Quartz

            step = 0
            while not stop.is_set():
                event = Quartz.CGEventCreateMouseEvent(
                    None, Quartz.kCGEventMouseMoved,
                    Quartz.CGPointMake(900 + (step % 16) * 5, 500 + (step // 16) * 5),
                    0,
                )
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
                step += 1
                time.sleep(0.04)

        thread = threading.Thread(target=wiggle, daemon=True)
        thread.start()
        try:
            super().start()
        finally:
            stop.set()
            thread.join(timeout=2.0)


def flash_screen(seconds: float = 0.4) -> float:
    """Show one full-screen magenta frame, and say when it went up.

    The recorder needs to know where the picture it captured begins. A colour
    that appears nowhere else, shown at a known wall-clock time, is a mark the
    recording can be searched for afterwards, which is what turns "the capture
    probably started around then" into a measured offset.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.setWindowFlags(
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool
    )
    window.setStyleSheet("background-color: #ff00ff;")
    screen = QApplication.primaryScreen()
    if screen is not None:
        window.setGeometry(screen.geometry())
    window.show()
    app.processEvents()
    started = time.time()
    time.sleep(seconds)
    window.hide()
    app.processEvents()
    time.sleep(0.2)
    return started


def find_flash(ffmpeg: str, raw: Path, limit: float = 90.0, fps: int = 10) -> float | None:
    """The time in the recording at which the magenta frame appears."""
    completed = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw),
         "-t", f"{limit:.1f}", "-vf", f"fps={fps},scale=64:36",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=False, timeout=300,
    )
    data = completed.stdout
    size = 64 * 36 * 3
    for index in range(len(data) // size):
        frame = data[index * size:(index + 1) * size]
        red = sum(frame[0::3]) / (size // 3)
        green = sum(frame[1::3]) / (size // 3)
        blue = sum(frame[2::3]) / (size // 3)
        if red > 170 and green < 90 and blue > 170:
            return index / fps
    return None


class WordSession(native.Session):
    """One take: the narration clock, plus the Word and ByteProof hands."""

    def __init__(self, clip: dict, plan: dict, work: Path, demo: WordDemo) -> None:
        super().__init__(
            clip, plan, work,
            plan["target"]["app_name"], plan["target"]["process"],
        )
        self.demo = demo

    # ------------------------------------------------------------ the stage
    async def prepare(self, word: bool = True, app: bool = True) -> None:
        """Put the stage together: Word set up, Settings already open.

        The app's window is used to open its own Settings window and is then
        parked off the frame, because the film is about the page: the tracked
        changes and the comment cards live in the margin the window would
        cover. All of this happens before the title card, so the film opens
        with the dialog in place rather than with windows jumping around.
        """
        target = self.plan["target"]
        windows = target["windows"]
        if app:
            self.marker("app: launch")
            self.demo.app_launch()
            self.marker("app: show window")
            self.demo.app_show_window()
            # Close and reopen once, off camera: the first close of a session
            # also shows the "ByteProof keeps running in the menu bar" hint,
            # and that hint is not part of any film. The second close, during
            # the take, is silent.
            self.demo.app_hide_window()
            self.demo.app_show_window()
            self.marker("app: place window")
            self.demo.app_place_window(*windows["app"])
        if word:
            source = Path(target["manuscript"])
            self.marker("word: close everything")
            self.demo.word_close_all()
            self.marker("word: open the take's copy")
            self.demo.word_open_copy(source)
            self.marker("word: place and set up")
            self.demo.word_place(*windows["word"])
            self.demo.word_zoom(130)
            self.demo.word_show_all_markup()
            self.demo.word_set_ribbon(collapsed=True)
            self.marker("stage ready")
        if app:
            self.marker("settings: open")
            self.demo.settings_open()
            self.demo.settings_place(*windows["settings"])
            self.demo.settings_scroll_to_bottom()
        await asyncio.sleep(0.4)

    async def settingsRow(self, label: str) -> None:
        """Ring the row this step is about. Settings is already open."""
        x, y = self.demo.settings_label_center(label)
        window = self.demo.app_window("AXDialog")
        assert window is not None
        left = x - 26
        rect = [left, y - 22, window["x"] + window["width"] - 18 - left, 46]
        self._cue(self.seconds(), set={"ring": self._frame_rect(rect)})
        await asyncio.sleep(0.4)

    async def settingsChoose(self, label: str, item: str) -> None:
        self.demo.settings_choose(label, item)
        await asyncio.sleep(0.3)

    async def settingsTemperature(self, value: float) -> None:
        self.demo.settings_temperature(value)
        await asyncio.sleep(0.3)

    async def settingsSave(self) -> None:
        self.demo.settings_close(save=True)
        self._cue(self.seconds(), clear=["ring"])
        await asyncio.sleep(0.4)

    async def hideAppWindow(self) -> None:
        """Let the panel go, so the status pill reports the proofread.

        A hidden window is how the app is normally used from the shortcut: the
        document keeps the screen, and the pill says what the app is doing.
        """
        self.marker("app: hide the window")
        await asyncio.to_thread(self.demo.app_hide_window)

    async def selectParagraph(self, key: str) -> None:
        """Select a named paragraph of page two, and ring it in the frame."""
        prefix = self.plan["paragraphs"][key]
        await asyncio.to_thread(self.demo.word_select_paragraph, prefix)
        await asyncio.sleep(0.4)

    async def wordFront(self) -> None:
        """Put Word in front, so the selection is the thing being looked at."""
        await asyncio.to_thread(self.demo.word, "activate")
        await asyncio.sleep(0.8)

    async def proofread(self) -> None:
        await asyncio.to_thread(self.demo.proofread)
        await asyncio.sleep(0.4)

    async def waitForEdits(self, expect_comment: bool = False) -> dict:
        outcome = await asyncio.to_thread(
            self.demo.wait_for_edits, 1, 300.0, 2.5, expect_comment
        )
        self.marker(
            f"revisions={outcome['revisions']} comments={outcome['comments']}"
        )
        return outcome

    async def hold(self, minimum: float = 0.0) -> None:
        """Wait out the spoken line, and at least ``minimum`` seconds."""
        started = self.seconds()
        await super().hold()
        remaining = minimum - (self.seconds() - started)
        if remaining > 0:
            await asyncio.sleep(remaining)


def body_runner(body: str, session: WordSession, clip: dict) -> None:
    helpers = {
        name: getattr(session, name)
        for name in (
            "prepare", "titles", "sayStep", "hold", "credits", "note", "noNote",
            "ringFrame", "unmark", "marker", "shot",
            "settingsRow", "settingsChoose", "settingsTemperature", "settingsSave",
            "hideAppWindow", "selectParagraph", "wordFront", "proofread",
            "waitForEdits", "wait",
        )
    }
    source = "async def __body():\n" + "".join(
        f"    {line}\n" for line in body.strip().splitlines()
    )
    namespace: dict = dict(helpers)
    # A clip's body is plan data, not user input: it comes from the plan file
    # in this repository and is run with only the session's helpers in scope.
    exec(compile(source, f"<{clip['name']}>", "exec"), namespace)  # noqa: S102
    asyncio.run(namespace["__body"]())


def load_plan(path: Path) -> dict:
    """Load the film plan, keeping the module itself for its paragraph map."""
    spec = importlib.util.spec_from_file_location("word_film_plan", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"{path} could not be loaded as a plan")
    module = importlib.util.module_from_spec(spec)
    sys.modules["word_film_plan"] = module
    spec.loader.exec_module(module)
    return {
        "groups": module.GROUPS,
        "subjects": module.SUBJECTS,
        "clips": module.CLIPS,
        "target": module.TARGET,
        "documents": {},
        "paragraphs": module.PARAGRAPHS,
        "source": str(path),
    }


def record_one(
    clip: dict, plan: dict, work: Path, settings: dict, camera: bool = True
) -> dict | None:
    """Film one take: the screen first, then the annotations over it."""
    target = plan["target"]
    ffmpeg = settings["ffmpeg"]

    demo = WordDemo(work, plan)
    session = WordSession(clip, plan, work, demo)
    session.durations = (
        [line["seconds"] for line in settings["lines"]]
        or [2.4] * (clip["steps"] + 2)
    )
    session.gap = native.STEP_BREATH if settings["lines"] else 1.0

    capture = LiveCapture(work / "screen.mp4", ffmpeg)
    if camera:
        capture.start()
        flash_at = flash_screen()
    else:
        flash_at = 0.0
    try:
        body_runner(clip["body"], session, clip)
    finally:
        if camera:
            capture.stop()
        try:
            demo.word_close_all()
        except Exception as problem:
            log(f"      · Word would not close its documents: {problem}")

    if not camera:
        log(
            f"      rehearsal: {session.seconds():.1f}s of steps, "
            f"{session.taken} spoken lines expected"
        )
        return None

    length = (session.finished or session.seconds()) + 0.4
    if length < 8.0:
        log(f"    ! {clip['name']}: the clip ran for only {length:.1f}s")
        return None

    raw = work / "screen.mp4"
    if not raw.exists():
        log(f"    ! {clip['name']}: no picture was captured")
        return None
    # The recording's own clock starts before the recorder noticed it; the
    # flash says where. Video time v of the flash was wall clock flash_at, so
    # the recording began at flash_at - v, and the film has to be cut to start
    # where the title card went up (session.zero).
    flashed = find_flash(ffmpeg, raw) if camera and flash_at else None
    if flashed is not None:
        begin = flash_at - flashed
        skip = max(0.0, session.zero - begin) if session.zero else 0.0
        log(
            f"      · the recording began {session.zero - begin:.1f}s of picture "
            f"before the title card (wall clock {session.zero - flash_at:.1f}s)"
        )
    else:
        if camera:
            log("      ! the timing flash was not found; using the old estimate")
        skip = max(0.0, session.zero - capture.started_at) if session.zero else 0.0
    filmed = native.duration_of(ffmpeg, raw) - skip
    if filmed < length * 0.9:
        log(f"    ! {clip['name']}: only {filmed:.1f}s of picture for {length:.1f}s of steps")
        return None

    stage = native.crop_screen(
        ffmpeg, raw, work / "stage.mp4", target["capture"], settings["scaling"], skip
    )
    if stage is None:
        return None
    recorded = native.annotate(clip, session, work, plan, ffmpeg, length)
    if recorded is None:
        return None
    screen, lead = recorded

    clip = dict(clip, subject_label=plan["subjects"].get(engine.subject_of(clip), ""))
    return native.build_film(clip, screen, session, work, ffmpeg, length, settings, lead)


def rehearsed_body(clip: dict) -> str:
    """The clip's body with a marker before each step, for a silent run."""
    return clip["body"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", required=True, help="the film plan (a .py file)")
    parser.add_argument("--out", default=None, help="where the films go")
    parser.add_argument("--only", help="comma-separated clip names or numbers")
    parser.add_argument("--group", help="only this group")
    parser.add_argument("--engine", default=engine.DEFAULT_ENGINE,
                        choices=("kokoro", "edge", "say"))
    parser.add_argument("--voice", default=None)
    parser.add_argument("--rate", type=int, default=engine.DEFAULT_RATE)
    parser.add_argument("--pace", type=int, default=engine.DEFAULT_PACE)
    parser.add_argument("--dark", type=int, default=120)
    parser.add_argument("--no-voice", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument(
        "--index-only", action="store_true",
        help="rebuild manifest, gallery and transcripts from the films already there",
    )
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument("--rehearse", action="store_true",
                        help="run one take with no camera and no narration")
    args = parser.parse_args()

    plan_path = Path(args.plan).expanduser().resolve()
    plan = load_plan(plan_path)
    clips, groups, subjects = plan["clips"], plan["groups"], plan["subjects"]

    if args.list:
        for clip in clips:
            log(f"{clip['group']:16} {clip['name']:26} {clip['card']['title']}")
        log(f"\n{len(clips)} clips")
        return 0

    ffmpeg = engine.which_ffmpeg()
    if not ffmpeg:
        raise SystemExit("ffmpeg is needed to write the films and was not found")

    wanted = {name.strip() for name in args.only.split(",")} if args.only else None
    if wanted:
        chosen = [
            clip for clip in clips
            if clip["name"] in wanted or clip["name"].split("-", 1)[0] in wanted
        ]
    elif args.group:
        chosen = [clip for clip in clips if args.group in clip["group"]]
    else:
        chosen = list(clips)
    if not chosen:
        log("no clips matched")
        return 1

    out = (
        Path(args.out).expanduser().resolve()
        if args.out else PROJECT / "demos" / "word"
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "clips").mkdir(exist_ok=True)

    scaling = plan["target"].get("scaling") or native.retina_scaling(ffmpeg)
    casting = {} if args.voice else engine.assign_voices(list(clips))
    brand = plan["target"].get("brand") or {}

    if args.index_only:
        # A set is recorded clip by clip, so the index is rebuilt from what is
        # on disk rather than from what this run happened to film.
        everything = []
        for clip in clips:
            film_path = out / "clips" / f"{clip['name']}.mp4"
            if not film_path.exists():
                log(f"      no film yet for {clip['name']}")
                continue
            everything.append({
                "clip": clip,
                "voice": (
                    args.voice or clip.get("voice")
                    or casting.get(clip["name"]) or ""
                ),
                "subject_label": plan["subjects"].get(engine.subject_of(clip), ""),
                "film": {
                    "name": clip["name"],
                    "file": film_path.name,
                    "poster": f"{clip['name']}.jpg",
                    "transcript": f"{clip['name']}.txt",
                    "subtitles": f"{clip['name']}.srt",
                    "seconds": round(native.duration_of(ffmpeg, film_path), 1),
                    "size_mb": round(film_path.stat().st_size / 1_048_576, 2),
                    "width": WIDTH,
                    "height": HEIGHT,
                },
            })
        if not everything:
            log("there are no films to index")
            return 1
        engine.write_master_transcript(out, everything, groups)
        engine.write_gallery(out, everything, groups, brand)
        (out / "manifest.json").write_text(
            json.dumps(
                {
                    "product": brand.get("name"),
                    "made": time.strftime("%Y-%m-%d"),
                    "screen_scale": scaling,
                    "clips": [
                        {
                            "name": item["film"]["name"],
                            "group": item["clip"]["group"],
                            "title": item["clip"]["card"]["title"],
                            "voice": item["voice"],
                            "seconds": item["film"]["seconds"],
                            "file": item["film"]["file"],
                        }
                        for item in everything
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log(f"the index lists {len(everything)} films")
        return 0

    work_root = out / ".raw"
    work_root.mkdir(exist_ok=True)

    word_rule_was_on = word_rule()
    word_rule(False)
    log(f"screen is {scaling}x; filming {len(chosen)} clips through a "
        f"{plan['target']['capture']['width']}x"
        f"{plan['target']['capture']['height']} point frame")
    results: list[dict] = []
    try:
        for number, clip in enumerate(chosen, start=1):
            # A voice asked for on the command line wins, then the voice the
            # plan gives this clip, then the seeded casting for the set.
            speaking = (
                args.voice
                or clip.get("voice")
                or casting.get(clip["name"])
                or engine.DEFAULT_VOICE
            )
            log(f"  {number:2}. {clip['name']}  ·  "
                f"{subjects.get(engine.subject_of(clip), '')}  ·  "
                f"{speaking if not args.no_voice else 'silent'}")
            work = Path(tempfile.mkdtemp(
                prefix=f"take-{clip['name'][:18]}-", dir=str(work_root)
            ))
            try:
                # The app reads its settings when it starts, so the base state
                # is written and the app restarted between takes. This happens
                # before the camera rolls.
                demo = WordDemo(work, plan)
                demo.app_quit()
                reset_base_settings()
                word_rule(False)
                demo.app_launch()

                settings = {
                    "ffmpeg": ffmpeg, "scaling": scaling, "dark": args.dark,
                    "out": out / "clips", "lines": [],
                }
                if not args.no_voice and not args.rehearse:
                    settings["lines"] = engine.synthesise(
                        clip["say"], work, speaking, args.rate, args.pace,
                        ffmpeg, args.engine,
                    )
                if args.rehearse:
                    film = record_one(clip, plan, work, settings, camera=False)
                    log("      rehearsal finished")
                    continue
                film = record_one(clip, plan, work, settings)
            finally:
                if not args.keep_raw and not args.rehearse:
                    shutil.rmtree(work, ignore_errors=True)
            if not film:
                log("      ! nothing was written")
                continue
            results.append({
                "clip": clip, "film": film, "voice": speaking,
                "subject_label": plan["subjects"].get(engine.subject_of(clip), ""),
            })
            log(f"      {film['file']}  {film['seconds']:g}s  {film['size_mb']} MB")
    finally:
        word_rule(word_rule_was_on)
        reset_base_settings()
        try:
            WordDemo(work_root, plan).word_set_ribbon(collapsed=False)
        except Exception:
            pass

    if results and not args.rehearse:
        engine.write_master_transcript(out, results, groups)
        engine.write_gallery(out, results, groups, brand)
        (out / "manifest.json").write_text(
            json.dumps(
                {
                    "product": brand.get("name"),
                    "made": time.strftime("%Y-%m-%d"),
                    "screen_scale": scaling,
                    "clips": [
                        {
                            "name": item["film"]["name"],
                            "group": item["clip"]["group"],
                            "title": item["clip"]["card"]["title"],
                            "voice": item["voice"],
                            "seconds": item["film"]["seconds"],
                            "file": item["film"]["file"],
                        }
                        for item in results
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log(f"the index lists {len(results)} films")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
