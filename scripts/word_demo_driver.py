#!/usr/bin/env python3
"""The hands for the Word demonstration films: Word, ByteProof, Settings.

Everything that touches the running applications lives here, so a rehearsal
and a take are driven by exactly the same code. Two rules run through it:

* a position is read back from the accessibility tree before it is clicked,
  never written down, because a dialog that moves is a click that lands on
  something else;
* a change is verified after it is made (the selection holds the paragraph,
  the combo holds the item, the document holds the revisions), because a film
  that quietly shows the wrong setting is worse than no film.

``rehearse_word_demo.py`` and ``record_word_demo.py`` both import this.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

WORD = "Microsoft Word"
APP = "ByteProof"
SETTINGS_WINDOW = "ByteProof Settings"

# Which control each row holds, in the order Settings builds them. The row is
# what a film rings; the index is how the combo that changed is spotted.
COMBO_ROWS = (
    ("Preferred Spelling", 0),
    ("Editing Style", 1),
    ("Add Reviewer Comment", 2),
    ("Document Context", 3),
)

KEY_HOME = 115
KEY_DOWN = 125
KEY_UP = 126
KEY_RETURN = 36
KEY_ESCAPE = 53


def _osascript(script: str, timeout: float = 90.0) -> str:
    """Run AppleScript, retrying the two errors that mean "try again".

    Driving a GUI meets them regularly: System Events can lose its connection
    while an application is starting or scrolling (-609), and an AppleEvent can
    time out while the target is busy (-1712). Neither says anything about the
    document, so the call is simply made again.
    """
    transient = (
        "-609", "-1712", "-1719", "-1728",
        "Connection is invalid", "AppleEvent timed out", "Invalid index",
    )
    last = ""
    for attempt in range(4):
        completed = subprocess.run(
            ["osascript", "-"], input=script.encode("utf-8"),
            capture_output=True, timeout=timeout, check=False,
        )
        if completed.returncode == 0:
            return completed.stdout.decode("utf-8")
        last = (completed.stderr or completed.stdout).decode("utf-8")[:400]
        if not any(marker in last for marker in transient):
            break
        time.sleep(0.6 + attempt * 0.6)
    raise RuntimeError(last)


def canonical(text: str) -> str:
    """One line ending, so two reads of the same text can be compared.

    Word counts a paragraph mark as one character, and osascript hands text
    back with either a carriage return or a line feed depending on how it was
    asked. Mapping both to a line feed keeps every offset Word's own, because
    the mapping never changes a length.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


class WordDemo:
    """The applications, driven the way a person drives them."""

    def __init__(self, work: Path, plan: dict) -> None:
        self.work = work
        self.plan = plan
        self.target = plan["target"]
        self.take: Path | None = None

    # ------------------------------------------------------------------ shell
    def ax(self, script: str, timeout: float = 60.0) -> str:
        return _osascript(
            f'tell application "System Events"\n{script}\nend tell', timeout
        )

    def word(self, script: str, timeout: float = 120.0) -> str:
        return _osascript(
            f'tell application "Microsoft Word"\n{script}\nend tell', timeout
        )

    def click(self, x: float, y: float) -> None:
        self.ax(f"click at {{{int(x)}, {int(y)}}}")

    def key(self, code: int, repeat: int = 1, pause: float = 0.12) -> None:
        for _ in range(max(1, repeat)):
            self.ax(f"key code {code}")
            time.sleep(pause)

    def keystroke(self, keys: str, using: tuple[str, ...] = ()) -> None:
        modifiers = (
            " using {" + ", ".join(f"{name} down" for name in using) + "}"
            if using else ""
        )
        self.ax(f"keystroke {json.dumps(keys)}{modifiers}")

    def _python(self, code: str, timeout: float = 60.0) -> None:
        subprocess.run([sys.executable, "-c", code], check=False, timeout=timeout)

    def move_pointer(self, x: float, y: float) -> None:
        self._python(
            "import Quartz\n"
            "event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved,"
            f" Quartz.CGPointMake({x}, {y}), 0)\n"
            "Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)\n"
        )

    def drag(self, x0: float, y0: float, x1: float, y1: float, steps: int = 24) -> None:
        """A real drag: press, move in steps, release."""
        self._python(f"""
import Quartz, time
def post(event):
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
def mouse(kind, x, y):
    post(Quartz.CGEventCreateMouseEvent(None, kind, Quartz.CGPointMake(x, y), 0))
mouse(Quartz.kCGEventMouseMoved, {x0}, {y0})
time.sleep(0.25)
mouse(Quartz.kCGEventLeftMouseDown, {x0}, {y0})
time.sleep(0.25)
for step in range(1, {steps} + 1):
    x = {x0} + ({x1} - {x0}) * step / {steps}
    y = {y0} + ({y1} - {y0}) * step / {steps}
    mouse(Quartz.kCGEventLeftMouseDragged, x, y)
    time.sleep(0.02)
time.sleep(0.2)
mouse(Quartz.kCGEventLeftMouseUp, {x1}, {y1})
time.sleep(0.3)
""")

    def scroll(self, x: float, y: float, amount: int, times: int = 6) -> None:
        self._python(f"""
import Quartz, time
point = Quartz.CGPointMake({x}, {y})
for _ in range({times}):
    event = Quartz.CGEventCreateScrollWheelEvent(
        None, Quartz.kCGScrollEventUnitPixel, 1, {amount})
    Quartz.CGEventSetLocation(event, point)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    time.sleep(0.09)
time.sleep(0.4)
""")

    # ------------------------------------------------------------------- Word
    def word_documents(self) -> list[str]:
        count = int(self.word("return count of documents").strip() or 0)
        names = []
        for index in range(1, count + 1):
            try:
                names.append(self.word(f"return name of document {index}").strip())
            except RuntimeError:
                pass
        return [name for name in names if name]

    def word_close_all(self) -> None:
        """Close every document, writing none of them to disk.

        ``close active document`` rather than a document by name: a name that
        has just been closed is an error, and the front document is always the
        one that is there.
        """
        for _ in range(12):
            if int(self.word("return count of documents").strip() or 0) == 0:
                return
            self.word_dismiss_prompts()
            self.word(
                """
                try
                  close active document saving no
                end try
                """
            )
            time.sleep(0.9)

    def word_dismiss_prompts(self) -> None:
        """Answer the one prompt a take can leave behind.

        A document with an open, unposted comment draft makes Word ask whether
        to go back to it; the take's copy is thrown away, so the draft goes with
        it. Anything else on screen is left alone and reported, because a
        recorder that clicks buttons at random is worse than one that stops.
        """
        for _ in range(3):
            dialogs = self.ax(
                f'''
                tell process {json.dumps(WORD)}
                  set out to ""
                  repeat with item1 in (every window)
                    if (subrole of item1) is "AXDialog" or ¬
                       (subrole of item1) is "AXSheet" then
                      set out to out & "dialog" & linefeed
                    end if
                  end repeat
                  return out
                end tell
                '''
            )
            if "dialog" not in dialogs:
                return
            texts = self.ax(
                f'''
                tell process {json.dumps(WORD)}
                  set out to ""
                  repeat with item1 in (every window)
                    if (subrole of item1) is "AXDialog" or ¬
                       (subrole of item1) is "AXSheet" then
                      repeat with item2 in (every static text of item1)
                        try
                          set out to out & (value of item2) & " "
                        end try
                      end repeat
                    end if
                  end repeat
                  return out
                end tell
                '''
            )
            if "comment" not in texts.lower():
                raise SystemExit(
                    f"Word is showing a dialog this recorder will not touch: "
                    f"{texts.strip()[:200]}"
                )
            self.ax(
                f'''
                tell process {json.dumps(WORD)}
                  repeat with item1 in (every window)
                    if (subrole of item1) is "AXDialog" or ¬
                       (subrole of item1) is "AXSheet" then
                      try
                        click button "No" of item1
                      end try
                    end if
                  end repeat
                end tell
                '''
            )
            time.sleep(0.8)

    def word_open_copy(self, source: Path, name: str | None = None) -> Path:
        """A fresh copy of the manuscript, so a take never edits the original."""
        self.work.mkdir(parents=True, exist_ok=True)
        self.take = self.work / (name or source.name)
        if self.take.exists():
            self.take.unlink()
        subprocess.run(["ditto", str(source), str(self.take)], check=True)
        self.word(f"open POSIX file {json.dumps(str(self.take))}")
        time.sleep(3.5)
        self.word("activate")
        return self.take

    def word_place(self, x: int, y: int, width: int, height: int) -> None:
        self.ax(
            f'''
            tell process {json.dumps(WORD)}
              set frontmost to true
              delay 0.2
              set position of window 1 to {{{x}, {y}}}
              set size of window 1 to {{{width}, {height}}}
            end tell
            '''
        )
        time.sleep(0.6)

    def word_zoom(self, percent: int) -> int:
        answer = self.word(
            f"""
            set percentage of zoom of view of active window to {percent}
            return percentage of zoom of view of active window
            """
        )
        return int(answer.strip())

    def word_show_all_markup(self) -> None:
        self.word(
            """
            try
              set show revisions of active document to true
            end try
            try
              set view type of view of active window to print view
            end try
            try
              set revisions mode of view of active window to mixed revisions
            end try
            """
        )

    def word_ribbon_state(self) -> str:
        """Whether Word's ribbon is showing: "expanded", "collapsed", or "".

        The Home tab's own Accessibility help says what pressing it would do,
        which is also the only place the ribbon's state is written down.
        """
        answer = self.ax(
            f'''
            tell process {json.dumps(WORD)}
              try
                repeat with item1 in (every radio button of tab group 1 of window 1)
                  try
                    set h to help of item1
                    if h ends with "ribbon" then return h
                  end try
                end repeat
              end try
              return ""
            end tell
            '''
        ).strip()
        if answer == "Collapse ribbon":
            return "expanded"
        if answer == "Expand ribbon":
            return "collapsed"
        return ""

    def word_set_ribbon(self, collapsed: bool) -> None:
        """Fold the ribbon away (or put it back), for a taller page."""
        want = "collapsed" if collapsed else "expanded"
        for _ in range(3):
            state = self.word_ribbon_state()
            if state == want or not state:
                return
            self.ax(
                f'''
                tell process {json.dumps(WORD)}
                  try
                    repeat with item1 in (every radio button of tab group 1 of window 1)
                      try
                        if (help of item1) ends with "ribbon" then
                          perform action "AXPress" of item1
                          exit repeat
                        end if
                      end try
                    end repeat
                  end try
                end tell
                '''
            )
            time.sleep(0.8)

    def word_paragraph_span(self, prefix: str) -> tuple[int, int, str]:
        text = canonical(
            self.word("return content of text object of active document")
        )
        position = 0
        for paragraph in text.split("\n"):
            if paragraph.startswith(prefix):
                return position, position + len(paragraph), paragraph
            position += len(paragraph) + 1
        raise SystemExit(f"no paragraph starts with {prefix!r}")

    def word_select_paragraph(self, prefix: str, settle: float = 1.0) -> tuple[int, int]:
        start, end, paragraph = self.word_paragraph_span(prefix)
        self.word(
            f"""
            set r to create range active document start {start} end {end}
            select r
            """
        )
        time.sleep(0.4)
        held = self.word(
            f"return content of (create range active document start {start} end {end})"
        )
        if canonical(held).strip("\n") != canonical(paragraph).strip("\n"):
            raise SystemExit("the selection does not hold the paragraph it should")
        self.word_wait_ready()
        time.sleep(settle)
        return start, end

    def word_revision_count(self) -> int:
        answer = self.word(
            """
            try
              return count of revisions of active document
            on error
              return "0"
            end try
            """
        )
        return int(answer.strip() or 0)

    def word_wait_ready(self, timeout: float = 12.0) -> bool:
        """Wait until Word answers a trivial question quickly.

        Selecting a paragraph far from the current view starts Word's own
        scroll animation, and a read that arrives during it fails with
        "Microsoft Word is not responding" even though nothing is wrong. Two
        fast answers in a row mean the window has stopped moving.
        """
        deadline = time.time() + timeout
        fast = 0
        while time.time() < deadline:
            started = time.monotonic()
            try:
                self.word("return count of documents", timeout=3.0)
            except RuntimeError:
                fast = 0
                time.sleep(0.4)
                continue
            if time.monotonic() - started < 0.4:
                fast += 1
                if fast >= 2:
                    return True
            else:
                fast = 0
            time.sleep(0.3)
        return False

    def word_comment_count(self) -> int:
        answer = self.word(
            """
            try
              return count of Word comments of active document
            on error
              return "0"
            end try
            """
        )
        return int(answer.strip() or 0)

    def word_track_changes(self) -> bool:
        return self.word("return track revisions of active document").strip() == "true"

    # --------------------------------------------------------------- ByteProof
    def app_running(self) -> bool:
        return subprocess.run(
            ["pgrep", "-x", APP], capture_output=True, check=False
        ).returncode == 0

    def app_launch(self, settle: float = 9.0) -> None:
        if self.app_running():
            return
        subprocess.run(["open", "-a", "/Applications/ByteProof.app"], check=False)
        for _ in range(int(settle * 4)):
            if self.app_running():
                break
            time.sleep(0.25)
        time.sleep(1.5)

    def app_quit(self) -> None:
        answer = subprocess.run(
            ["pgrep", "-x", APP], capture_output=True, text=True, check=False
        )
        for pid in answer.stdout.split():
            subprocess.run(["kill", "-TERM", pid], check=False)
        for _ in range(60):
            if not self.app_running():
                return
            time.sleep(0.25)

    def app_windows(self) -> list[dict]:
        """EveryByteProof window, with the geometry the accessibility tree gives.

        AppleScript joins a list into text using the text item delimiters, and
        the default is nothing at all: {860, 95} becomes "86095". The
        delimiters are set for the read and put back, which is the only way the
        two numbers can be told apart afterwards.
        """
        raw = self.ax(
            f'''
            tell process {json.dumps(APP)}
              set out to ""
              set saved to AppleScript's text item delimiters
              set AppleScript's text item delimiters to ";"
              repeat with item1 in (every window)
                set nm to ""
                try
                  set nm to name of item1
                end try
                set out to out & nm & "|" & (subrole of item1) & "|"
                try
                  set out to out & ((position of item1) as text) & "|" & ¬
                    ((size of item1) as text)
                on error
                  set out to out & "none"
                end try
                set out to out & linefeed
              end repeat
              set AppleScript's text item delimiters to saved
              return out
            end tell
            '''
        )
        windows: list[dict] = []
        for line in raw.splitlines():
            parts = line.split("|")
            if len(parts) != 4:
                continue
            if parts[2] == "none" or parts[3] == "none":
                continue
            try:
                position = [int(float(part)) for part in parts[2].split(";")]
                size = [int(float(part)) for part in parts[3].split(";")]
            except ValueError:
                # A window with no geometry (an off-screen helper, or one that
                # is still being built) is not a window a film can use.
                continue
            if len(position) != 2 or len(size) != 2:
                continue
            windows.append(
                {
                    "name": parts[0], "subrole": parts[1],
                    "x": position[0], "y": position[1],
                    "width": size[0], "height": size[1],
                }
            )
        return windows

    def app_window(self, subrole: str = "AXStandardWindow") -> dict | None:
        for window in self.app_windows():
            if window["subrole"] == subrole:
                return window
        return None

    def app_show_window(self) -> None:
        """Open the proofreading window the way a person does: its hotkey."""
        self.ax('keystroke ";" using {command down, shift down}')
        for _ in range(60):
            if self.app_window() is not None:
                break
            time.sleep(0.25)
        time.sleep(0.8)

    def app_place_window(self, x: int, y: int, width: int, height: int) -> dict:
        for attempt in range(3):
            try:
                self.ax(
                    f'''
                    tell process {json.dumps(APP)}
                      set frontmost to true
                      delay 0.2
                      set position of window 1 to {{{x}, {y}}}
                      set size of window 1 to {{{width}, {height}}}
                    end tell
                    '''
                )
            except RuntimeError:
                pass
            time.sleep(0.8)
            window = self.app_window()
            if window is not None:
                return window
            # The window can take a moment to come back after it was hidden;
            # the open shortcut is the door that shows it.
            self.app_show_window()
        raise SystemExit("the ByteProof window is not on screen")

    def app_hide_window(self) -> None:
        self.ax(
            f'''
            tell process {json.dumps(APP)}
              try
                click (first button of window 1 whose subrole is "AXCloseButton")
              end try
            end tell
            '''
        )
        time.sleep(0.8)

    def app_park_window(self, x: int = 1502, y: int = 95) -> None:
        """Move the app's window off the frame, the way a person tidies up.

        The film is about the document while the proofread runs; the window
        that holds the diff pane would cover the page's margin, which is where
        the tracked changes and the comments appear. The status pill still
        reports what the app is doing.
        """
        self.ax(
            f'''
            tell process {json.dumps(APP)}
              try
                set position of window 1 to {{{x}, {y}}}
              end try
            end tell
            '''
        )
        time.sleep(0.6)

    # ---------------------------------------------------------------- Settings
    def settings_open(self) -> None:
        self.ax(
            f'''
            tell process {json.dumps(APP)}
              set frontmost to true
              delay 0.2
              click button "Settings" of window 1
            end tell
            '''
        )
        for _ in range(60):
            if self.app_window("AXDialog") is not None:
                break
            time.sleep(0.25)
        # The click opens the dialog behind whatever was in front; raising it
        # is what a person sees when they click the button.
        self.ax(
            f'''
            tell process {json.dumps(APP)}
              set frontmost to true
              try
                perform action "AXRaise" of window {json.dumps(SETTINGS_WINDOW)}
              end try
            end tell
            '''
        )
        time.sleep(1.0)

    def settings_place(self, x: int, y: int, width: int, height: int) -> None:
        self.ax(
            f'''
            tell process {json.dumps(APP)}
              set frontmost to true
              tell window {json.dumps(SETTINGS_WINDOW)}
                set position to {{{x}, {y}}}
                set size to {{{width}, {height}}}
                try
                  perform action "AXRaise"
                end try
              end tell
            end tell
            '''
        )
        time.sleep(0.7)

    def settings_close(self, save: bool) -> None:
        button = "Save" if save else "Cancel"
        self.ax(
            f'''
            tell process {json.dumps(APP)}
              tell window {json.dumps(SETTINGS_WINDOW)}
                click button "{button}" of group 2
              end tell
            end tell
            '''
        )
        for _ in range(60):
            if self.app_window("AXDialog") is None:
                return
            time.sleep(0.25)
        raise SystemExit("the Settings window did not close")

    def settings_scroll_to_bottom(self) -> None:
        window = self.app_window("AXDialog")
        if window is None:
            raise SystemExit("Settings is not open")
        self.scroll(
            window["x"] + int(window["width"] * 0.75),
            window["y"] + int(window["height"] * 0.6),
            -260, times=8,
        )

    def settings_combo_values(self) -> list[str]:
        raw = self.ax(
            f'''
            tell process {json.dumps(APP)}
              tell window {json.dumps(SETTINGS_WINDOW)}
                set out to ""
                repeat with item1 in (every menu button of group 1)
                  set out to out & (name of item1) & linefeed
                end repeat
                return out
              end tell
            end tell
            '''
        )
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def settings_label_center(self, label: str) -> tuple[int, int]:
        raw = self.ax(
            f'''
            tell process {json.dumps(APP)}
              tell window {json.dumps(SETTINGS_WINDOW)}
                set saved to AppleScript's text item delimiters
                set AppleScript's text item delimiters to ";"
                repeat with item1 in (every static text of group 1)
                  try
                    if (value of item1) starts with {json.dumps(label)} then
                      set out to ((position of item1) as text) & "|" & ¬
                        ((size of item1) as text)
                      set AppleScript's text item delimiters to saved
                      return out
                    end if
                  end try
                end repeat
                set AppleScript's text item delimiters to saved
              end tell
            end tell
            '''
        )
        position, size = raw.split("|")
        x, y = [int(float(part)) for part in position.split(";")]
        height = int(float(size.split(";")[1]))
        return x, y + height // 2

    def settings_combo_center(self, label: str) -> tuple[int, int]:
        """The dropdown in a row: right-aligned, so it is found from the edge."""
        window = self.app_window("AXDialog")
        if window is None:
            raise SystemExit("Settings is not open")
        _, y = self.settings_label_center(label)
        return window["x"] + window["width"] - 171, y

    def settings_choose(self, label: str, item: str) -> None:
        """Pick a dropdown item, and check that it is the one that changed.

        The list opens above or below depending on the room under the row, so
        the item is chosen from the keyboard: the list starts on its first
        entry and the arrows walk to the wanted one. That keeps the choice
        independent of where the popup lands, and the popup itself is still
        the application's own.
        """
        choices = {
            "Preferred Spelling": ["UK/AU/NZ", "US English"],
            "Editing Style": ["Precise (Minimal Changes)", "Creative (Rewrite)"],
            "Add Reviewer Comment": ["None", "Language", "Technical (Reviewer)"],
            "Document Context": [
                "General Editing", "Email Editing", "PhD Thesis Chapter",
                "Academic Journal (Top-Tier)",
            ],
        }[label]
        index = [row for row, _ in COMBO_ROWS].index(label)
        before = self.settings_combo_values()
        x, y = self.settings_combo_center(label)
        self.click(x, y)
        time.sleep(0.9)
        self.key(KEY_HOME)
        self.key(KEY_DOWN, repeat=choices.index(item), pause=0.25)
        self.key(KEY_RETURN)
        time.sleep(0.8)
        after = self.settings_combo_values()
        if after[index] != item:
            raise SystemExit(
                f"{label}: wanted {item!r}, Settings now shows {after[index]!r}"
            )
        if before[index] == after[index]:
            raise SystemExit(f"{label}: the value did not change")

    def settings_slider(self) -> dict:
        raw = self.ax(
            f'''
            tell process {json.dumps(APP)}
              tell window {json.dumps(SETTINGS_WINDOW)}
                set saved to AppleScript's text item delimiters
                set AppleScript's text item delimiters to ";"
                set item1 to slider 1 of group 1
                set out to ((position of item1) as text) & "|" & ¬
                  ((size of item1) as text) & "|" & (value of item1)
                set AppleScript's text item delimiters to saved
                return out
              end tell
            end tell
            '''
        )
        position, size, current = raw.split("|")
        x, y = [int(float(part)) for part in position.split(";")]
        width, height = [int(float(part)) for part in size.split(";")]
        return {
            "x": x, "y": y, "width": width, "height": height,
            "value": int(float(current)),
        }

    def settings_temperature(self, value: float) -> float:
        """Drag the freedom slider, then nudge it until the number agrees."""
        wanted = round(value * 10)
        handle = 10
        current = self.settings_slider()
        travel = current["width"] - 2 * handle
        middle = current["y"] + current["height"] // 2
        start_x = current["x"] + handle + travel * current["value"] / 20.0
        target_x = current["x"] + handle + travel * wanted / 20.0
        self.drag(start_x, middle, target_x, middle)
        time.sleep(0.6)
        current = self.settings_slider()
        rounds = 0
        while current["value"] != wanted:
            rounds += 1
            if rounds > 6:
                raise SystemExit("the freedom slider would not settle")
            direction = KEY_DOWN if current["value"] < wanted else KEY_UP
            handle_x = current["x"] + handle + travel * current["value"] / 20.0
            self.click(handle_x, middle)
            self.key(direction, repeat=abs(current["value"] - wanted), pause=0.2)
            time.sleep(0.5)
            current = self.settings_slider()
        return wanted / 10.0

    # ------------------------------------------------------------------ flow
    def proofread(self) -> None:
        """Press the proofread shortcut with Word in front, as a person would."""
        self.word("activate")
        self.word_wait_ready()
        time.sleep(0.8)
        selected = self.word("return content of text object of selection").strip()
        if not selected:
            raise SystemExit(
                "nothing is selected in Word, so the proofread would fail"
            )
        self.keystroke("'", using=("command", "shift"))

    def app_status_text(self) -> str:
        """What the window says, or "" when the window is not on screen.

        The app is normally used with its window hidden, and the pill is the
        only thing it draws then. A missing window is not a failure: the
        document's own revision count is the signal the wait depends on.
        """
        try:
            return self.ax(
                f'''
                tell process {json.dumps(APP)}
                  set out to ""
                  try
                    repeat with item1 in (every static text of window 1)
                      try
                        set out to out & (value of item1) & linefeed
                      end try
                    end repeat
                  end try
                  return out
                end tell
                '''
            )
        except RuntimeError:
            return ""

    def wait_for_edits(
        self,
        minimum: int = 1,
        timeout: float = 240.0,
        settle: float = 2.5,
        expect_comment: bool = False,
    ) -> dict:
        """Wait until the document holds the edits and has stopped growing.

        The app applies everything in one pass, so the honest signal is the
        document's own revision count, not a guess about how long a model
        takes. The window's status line is read too, because a run that failed
        says so there rather than in Word.
        """
        deadline = time.time() + timeout
        last = -1
        stable_since: float | None = None
        while time.time() < deadline:
            count = self.word_revision_count()
            status = self.app_status_text()
            if "Error" in status or "not responding" in status:
                raise SystemExit(f"the app reported: {status.strip()[:200]}")
            if count >= minimum:
                if count == last:
                    if stable_since is None:
                        stable_since = time.time()
                    elif time.time() - stable_since >= settle:
                        break
                else:
                    last = count
                    stable_since = None
            time.sleep(0.6)
        else:
            raise SystemExit(
                f"the document never took {minimum} revisions "
                f"(it holds {self.word_revision_count()})"
            )
        if expect_comment:
            comment_deadline = time.time() + 90
            while time.time() < comment_deadline and self.word_comment_count() < 1:
                time.sleep(0.7)
        time.sleep(1.2)
        return {
            "revisions": self.word_revision_count(),
            "comments": self.word_comment_count(),
        }
