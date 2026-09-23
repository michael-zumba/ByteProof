# pyright: reportMissingModuleSource=false
import html
import json
import os
import platform
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

WD_WITH_IN_TABLE = 12  # Word constant: wdWithInTable

# WdStoryType values the live preview has to tell apart.
WD_MAIN_TEXT_STORY = 1
WD_COMMENTS_STORY = 4

# Where the current selection lives (WordIntegration.selection_scope).
#   main        - the document body: proofread and write through ranges
#   comments    - a Word comment: proofread, rewritten as a whole selection
#   whole_table - a complete table is selected: structure, never proofread
#   other_story - headers, footers, footnotes: no safe write path
SCOPE_MAIN = "main"
SCOPE_COMMENTS = "comments"
SCOPE_WHOLE_TABLE = "whole_table"
SCOPE_OTHER_STORY = "other_story"

# Word can block on a modal dialog (file in use, Protected View, password,
# macro consent). Without a timeout the worker waits forever and the feature
# silently hangs, so every AppleScript call is bounded.
APPLESCRIPT_TIMEOUT_S = 30.0
# Short reads (document/selection state) should fail fast rather than freeze.
APPLESCRIPT_READ_TIMEOUT_S = 10.0
# The live poll reads the selection on every tick: a busy Word must not
# stall the loop, so the poll read is bounded much tighter. A healthy read
# answers in well under 200 ms; anything slower means Word is busy, and the
# poll backs off instead of blocking the UI thread - at 3 s a single stuck
# tick was long enough to feel like the app had frozen.
WORD_POLL_READ_TIMEOUT_S = 1.5

# Mapping visible offsets to document positions is about ten in-process reads
# per offset, so a healthy Word answers in well under a second. The bound keeps
# a busy Word from blocking the UI thread while the apply waits.
WORD_MAP_TIMEOUT_S = 5.0

# How many characters of a field's visible result the macOS fallback scan may
# read before giving up. A page of text is roughly 3,000-3,500 characters, so
# 4,000 covers any realistic citation/field result while keeping the scan
# bounded. The scan normally stops at the field's end character, so this is
# only a safety net for fields whose visible result is a full page or longer.
FIELD_RESULT_SCAN_LIMIT = 4000

# Word for Mac cannot create a comment through its AppleScript dictionary, so
# the box is opened through the user interface and filled from the clipboard.
# Current builds open a *draft* box: it sits on screen ready for text while
# ``count of comments`` stays flat until the draft is posted with Cmd+Return.
# These bounds cover the box appearing, and the posted comment becoming visible.
COMMENT_BOX_TIMEOUT_S = 8.0
COMMENT_BOX_POLL_S = 0.4
COMMENT_POST_TIMEOUT_S = 3.0
COMMENT_POST_POLL_S = 0.3
# Time Word's composer gets to settle after its value is written through
# Accessibility and before the box is posted.
AX_COMMENT_SETTLE_S = 0.4
# Time Word gets to give the comment box keyboard focus after it is pressed.
AX_COMMENT_FOCUS_TIMEOUT_S = 2.0

# Accessibility walk bounds for finding the comment box and the ribbon button.
# A Word window carries a few hundred to a few thousand elements; both targets
# sit shallow in practice and the walk stops as soon as it is found.
AX_WALK_MAX_NODES = 4000
AX_WALK_MAX_DEPTH = 24

WORD_BUNDLE_ID = "com.microsoft.Word"


def _word_pid() -> int | None:
    """Process id of Microsoft Word, or None when it is not running."""
    try:
        from AppKit import NSWorkspace

        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if str(app.bundleIdentifier() or "") == WORD_BUNDLE_ID:
                return int(app.processIdentifier())
    except Exception:
        pass
    try:
        found = subprocess.run(
            ["pgrep", "-x", "Microsoft Word"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        if found:
            return int(found[0])
    except Exception:
        pass
    return None


def _ax_attribute(AS: Any, element: Any, name: str) -> Any:
    """Read one Accessibility attribute, or None when it is not there."""
    try:
        err, value = AS.AXUIElementCopyAttributeValue(element, name, None)
        if err == 0:
            return value
    except Exception:
        pass
    return None


def _ax_find(AS: Any, root: Any, matches: Callable[[Any], bool]) -> Any | None:
    """Depth-first search for the first element the predicate accepts.

    Bounded in both nodes and depth: the comment box and the ribbon button are
    both shallow, and a Word window can otherwise be walked for a long time.
    """
    stack: list[tuple[Any, int]] = [(root, 0)]
    visited = 0
    while stack and visited < AX_WALK_MAX_NODES:
        node, depth = stack.pop()
        visited += 1
        try:
            if matches(node):
                return node
        except Exception:
            pass
        if depth >= AX_WALK_MAX_DEPTH:
            continue
        children = _ax_attribute(AS, node, "AXChildren")
        if not children:
            continue
        try:
            for child in list(children):
                stack.append((child, depth + 1))
        except TypeError:
            continue
    return None


def _comment_box_open(pid: int) -> bool:
    """Whether Word is showing a comment box for a document.

    The box is a *draft* on current builds: it is open and ready for text
    while ``count of comments`` has not moved at all. Its own Accessibility
    elements are therefore the only reliable "the box is open" signal — and
    that signal has to be right, because pasting without it would type the
    comment into the manuscript instead.

    Only the window of the *active document* is searched: a draft left open
    in another document must never receive this document's note.
    """
    try:
        import ApplicationServices as AS
    except Exception:
        return False

    def is_box(node: Any) -> bool:
        help_text = str(_ax_attribute(AS, node, "AXHelp") or "")
        title = str(_ax_attribute(AS, node, "AXTitle") or "")
        # The composer's post button only exists while a draft is open, and
        # the comment box itself is the text area Word labels as a comment.
        if help_text.startswith("Post comment") or title == "Post comment":
            return True
        description = str(_ax_attribute(AS, node, "AXDescription") or "")
        role = str(_ax_attribute(AS, node, "AXRole") or "")
        return role == "AXTextArea" and "comment" in description.lower()

    window = _comment_box_window(AS, int(pid))
    if window is None:
        return False
    return _ax_find(AS, window, is_box) is not None


def _active_document_name() -> str:
    """Word's active document name, or "" when it cannot be read."""
    try:
        out = subprocess.run(
            [
                "osascript",
                "-e",
                (
                    'tell application "Microsoft Word" to return '
                    "name of active document"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=APPLESCRIPT_READ_TIMEOUT_S,
            check=False,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def _comment_box_window(AS: Any, pid: int) -> Any | None:
    """The window of Word's active document, where the comment box lives."""
    app = AS.AXUIElementCreateApplication(int(pid))
    document = _active_document_name()
    windows = _ax_attribute(AS, app, "AXWindows")
    if not windows:
        return None
    try:
        candidates = list(windows)
    except TypeError:
        return None
    if not candidates:
        return None
    if document:
        for window in candidates:
            title = str(_ax_attribute(AS, window, "AXTitle") or "")
            if title == document or document in title:
                return window
        # The active document has no window of its own on screen: reading or
        # writing its comment box would land somewhere else.
        return None
    # Without a name to match, stay with the window Word put in front.
    return candidates[0]


def _press_new_comment_button(pid: int) -> bool:
    """Press Review ▸ New Comment through Accessibility.

    The Insert menu and the Review ribbon are two doors to the same command.
    Some Word builds ignore the menu item when it is driven by System Events,
    so the ribbon press is the second attempt; it reports False when neither
    the button nor the Review tab is on screen.
    """
    try:
        import ApplicationServices as AS
    except Exception:
        return False

    app = AS.AXUIElementCreateApplication(int(pid))
    windows = _ax_attribute(AS, app, "AXWindows")
    if not windows:
        return False
    try:
        windows = list(windows)
    except TypeError:
        return False
    if not windows:
        return False
    window = windows[0]

    def titled(name: str) -> Callable[[Any], bool]:
        return lambda node: str(
            _ax_attribute(AS, node, "AXTitle") or ""
        ) == name

    button = _ax_find(AS, window, titled("New Comment"))
    if button is None:
        # The ribbon shows one tab's controls at a time, and the comment
        # button lives on the Review tab.
        tab = _ax_find(AS, window, titled("Review"))
        if tab is None:
            return False
        try:
            AS.AXUIElementPerformAction(tab, "AXPress")
        except Exception:
            return False
        time.sleep(0.4)
        button = _ax_find(AS, window, titled("New Comment"))
    if button is None:
        return False
    try:
        return int(AS.AXUIElementPerformAction(button, "AXPress")) == 0
    except Exception:
        return False


def _comment_draft_box(pid: int) -> tuple[Any, Any] | None:
    """Word's open draft comment box as (text area, post button).

    The box is the group that carries the composer's Post comment button; its
    text area is the one beside it. Both are read through Accessibility, which
    is the only view of the box that exists before the comment is posted.
    """
    try:
        import ApplicationServices as AS
    except Exception:
        return None

    window = _comment_box_window(AS, int(pid))
    if window is None:
        return None

    def role_of(node: Any) -> str:
        return str(_ax_attribute(AS, node, "AXRole") or "")

    post = _ax_find(
        AS,
        window,
        lambda node: str(_ax_attribute(AS, node, "AXTitle") or "")
        == "Post comment"
        or str(_ax_attribute(AS, node, "AXHelp") or "").startswith(
            "Post comment"
        ),
    )
    if post is None:
        return None
    group = _ax_attribute(AS, post, "AXParent")
    if group is None:
        return None
    area = _ax_find(AS, group, lambda node: role_of(node) == "AXTextArea")
    if area is None:
        return None
    return area, post


def _fill_comment_box(pid: int, comment_text: str) -> bool:
    """Type the comment into the open box and post it.

    Word's composer refuses an Accessibility value write (it is a web view),
    so the note is pasted after the box is given keyboard focus — and only
    after, because a paste with the focus elsewhere lands in the document.
    The text is read back from the box before Post is pressed, and Word's own
    Post button stays disabled until the box has text.
    """
    box = _comment_draft_box(pid)
    if box is None:
        return False
    area, post = box
    try:
        import ApplicationServices as AS
    except Exception as exc:
        _log_word(f"  Comment box unavailable: {exc}")
        return False

    if not _focus_comment_box(area):
        _log_word("  Comment box would not take keyboard focus")
        return False

    _paste_comment_text()
    time.sleep(AX_COMMENT_SETTLE_S)

    probe = comment_text.strip()[:24]
    value = str(_ax_attribute(AS, area, "AXValue") or "")
    if probe and probe not in value:
        _log_word("  Comment box did not take the pasted note; not posting")
        return False

    # The box holds the note: post it the way a reader would, with Word's own
    # button (Cmd+Return leaves the box empty on this build).
    try:
        AS.AXUIElementPerformAction(post, "AXPress")
    except Exception as exc:
        _log_word(f"  Posting the comment failed: {exc}")
        return False
    return True


def _comment_box_has_focus(pid: int) -> bool:
    """Whether Word's open comment box holds keyboard focus.

    Only then may a pasted keystroke be trusted to land in the comment: with
    the focus anywhere else the same paste goes into the manuscript, which is
    the outcome the caller refuses to risk.
    """
    box = _comment_draft_box(pid)
    if box is None:
        return False
    area, _post = box
    try:
        import ApplicationServices as AS

        return str(_ax_attribute(AS, area, "AXFocused") or "") == "True"
    except Exception:
        return False


def _focus_comment_box(area: Any) -> bool:
    """Put the keyboard focus into Word's open comment box.

    The composer is a web view: it only accepts typed text while it holds
    focus, and its Post button stays disabled until the box has text. Pressing
    the box and setting its focus attribute are both attempted; Word honours
    one of them even when it reports the other as unsupported.
    """
    try:
        import ApplicationServices as AS

        if str(_ax_attribute(AS, area, "AXFocused") or "") == "True":
            return True
        try:
            AS.AXUIElementPerformAction(area, "AXPress")
        except Exception:
            pass
        try:
            AS.AXUIElementSetAttributeValue(area, "AXFocused", True)
        except Exception:
            pass
        deadline = time.monotonic() + AX_COMMENT_FOCUS_TIMEOUT_S
        while time.monotonic() < deadline:
            if str(_ax_attribute(AS, area, "AXFocused") or "") == "True":
                return True
            time.sleep(0.1)
    except Exception:
        return False
    return False


def _paste_comment_text() -> None:
    """Paste the clipboard into the focused comment box."""
    script = '''
        tell application "System Events"
            tell process "Microsoft Word"
                set frontmost to true
                delay 0.2
                keystroke "v" using {command down}
                delay 0.4
            end tell
        end tell
        return "OK"
    '''
    try:
        subprocess.run(
            ["osascript", "-"],
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=APPLESCRIPT_READ_TIMEOUT_S,
            check=False,
        )
    except Exception as exc:
        _log_word(f"  Comment paste failed: {exc}")


def _comment_posted_in_pane(pid: int, comment_text: str) -> bool:
    """Whether Word's comments pane shows the note as a posted comment.

    Modern Word comments are invisible to AppleScript: ``count of comments``
    stays at zero for a comment that is plainly posted in the pane, with the
    card reading "Comment from <author>. <text>. On <date>". That card is the
    evidence a comment landed, and it is deliberately specific — a note that
    was accidentally typed into the document does not produce one.
    """
    probe = comment_text.strip()[:24]
    if not probe:
        return False
    try:
        import ApplicationServices as AS
    except Exception:
        return False
    window = _comment_box_window(AS, int(pid))
    if window is None:
        return False

    def is_posted_card(node: Any) -> bool:
        if str(_ax_attribute(AS, node, "AXRole") or "") != "AXGroup":
            return False
        description = str(_ax_attribute(AS, node, "AXDescription") or "")
        return description.startswith("Comment from ") and probe in description

    return _ax_find(AS, window, is_posted_card) is not None


class WordBusyError(RuntimeError):
    """Word did not respond in time (usually a modal dialog is open)."""


def _log_word(message: str) -> None:
    """Record a Word diagnostic where a windowed build can still see it.

    The frozen app has no console, so the previous ``print()`` calls were
    invisible in the field. Diagnostics go to the same capture log the live
    service uses.
    """
    try:
        from .generic_editing import _debug_log

        _debug_log(f"WORD: {message}")
    except Exception:
        pass


def _normalize_for_compare(text: str) -> str:
    """Normalise text for Word read-back comparison."""
    from .utils import normalize_text

    return normalize_text(text or "", collapse_whitespace=True)


def _applescript_status(raw: str) -> str:
    """Extract the status token from a guarded script's output."""
    return (raw or "").strip().splitlines()[-1].strip() if raw else ""


def _applescript_quote(value: str) -> str:
    """Quote a Python string for safe interpolation into AppleScript."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


# Word ends every table cell (and row) with a Chr(7) cell mark. It is part of
# the text a cell selection reports, so a suggestion can carry it - and a
# suggestion that drops it merges the cell with the next one. Table text is
# editable, the table itself is not.
CELL_MARK = "\x07"


def cell_mark_mismatch(before_text: str | None, replacement: str) -> bool:
    """Whether writing this replacement would restructure a table."""
    if not before_text or CELL_MARK not in before_text:
        return False
    return before_text.count(CELL_MARK) != replacement.count(CELL_MARK)


TABLE_STRUCTURE_MESSAGE = (
    "That change would alter the table structure — edit the text inside the "
    "cells."
)


class FieldSpan(NamedTuple):
    """A Word field inside the current selection.

    doc_start is the position of the field begin character, doc_end is the
    position after the field end character, and result_text is the visible
    result Word displays for the field (e.g. an EndNote citation).
    """

    doc_start: int
    doc_end: int
    result_text: str


def _drop_nested_fields(
    fields: list[tuple[int, int, int, int, str]],
) -> list[tuple[int, int, int, int, str]]:
    """Keep only the outermost Word fields.

    Word reports nested fields (an EndNote citation can contain an inner
    field whose code lies inside the outer field's code). The outer field's
    range already accounts for the inner field's hidden characters, so
    keeping both would double-count hidden offsets and shift every edit that
    follows the citation.
    """
    outermost: list[tuple[int, int, int, int, str]] = []
    for candidate in fields:
        cs, ce, *_ = candidate
        if cs <= 0 or ce <= 0:
            continue
        nested = any(
            other_cs < cs and ce <= other_ce
            for other_cs, other_ce, *_ in fields
            if (other_cs, other_ce) != (cs, ce)
        )
        if not nested:
            outermost.append(candidate)
    return outermost


class WordIntegration:
    """Abstract base class for Microsoft Word interaction."""

    def apply_live_edit(
        self,
        selection_start: int,
        rel_start: int,
        rel_end: int,
        replacement: str,
        before_text: str | None = None,
        expected_document: str | None = None,
    ) -> tuple[bool, str]:
        raise NotImplementedError

    def set_live_underline(
        self, selection_start: int, rel_start: int, rel_end: int, enable: bool
    ) -> tuple[bool, str]:
        raise NotImplementedError

    def clear_live_underlines(self) -> None:
        raise NotImplementedError

    def ensure_ready(self) -> None:
        raise NotImplementedError

    def ensure_track_changes_enabled(self) -> None:
        raise NotImplementedError

    def ensure_track_changes_disabled(self) -> None:
        raise NotImplementedError

    def get_selection_info(self) -> tuple[str, int, int, str, str]:
        """Returns (text, start_index, end_index, context_before, context_after)"""
        raise NotImplementedError

    def selection_scope(self) -> str:
        """Where the current selection lives: see the SCOPE_* constants.

        The document body is the only story whose character offsets can be
        written back through document ranges, so everything else has to be
        either handled on its own terms (comments) or left alone.
        """
        raise NotImplementedError

    def replace_comment_selection(
        self,
        expected_text: str,
        new_text: str,
        expected_document: str | None = None,
    ) -> tuple[bool, str]:
        """Replace the text of a selection that sits in a Word comment."""
        raise NotImplementedError

    def read_range_text(self, start: int, end: int) -> str:
        """The document text at an absolute range, or "" when unreadable.

        Read right after a live edit: Word normalises what it is given, so the
        only truth about what an edit left behind is a read of the range it
        wrote. Undo's guard compares against that text.
        """
        raise NotImplementedError

    def add_comment(self, comment_text: str) -> None:
        raise NotImplementedError

    def delete_range(self, abs_start: int, abs_end: int) -> None:
        raise NotImplementedError

    def insert_at_position(self, abs_pos: int, text: str) -> None:
        raise NotImplementedError

    def replace_range(self, abs_start: int, abs_end: int, text: str) -> None:
        raise NotImplementedError

    def replace_selection_content(self, new_text: str) -> None:
        raise NotImplementedError

    def set_selection_range(self, start: int, end: int) -> None:
        """Restore the Word selection to ``[start, end)`` (best effort)."""
        raise NotImplementedError

    def selection_has_fields(self) -> bool:
        raise NotImplementedError

    def get_selection_field_spans(self) -> list[FieldSpan]:
        """Return the fields in the current selection as FieldSpan items."""
        raise NotImplementedError

    def get_selection_hidden_spans(
        self,
        start: int = 0,
        end: int = 0,
        exclude_spans: list[tuple[int, int]] | None = None,
        max_hidden: int = 0,
    ) -> list[tuple[int, int]]:
        """Return document positions of text hidden from the selection text.

        Tracked deletions still occupy Word's internal character positions but
        are omitted from Range.Text / AppleScript ``content``. Every such span
        must be compensated for when mapping visible-text offsets back to
        absolute document positions.
        """
        raise NotImplementedError

    def live_doc_positions(
        self,
        sel_start: int,
        sel_end: int,
        visible_offsets: Sequence[int],
    ) -> dict[int, int] | None:
        """Map visible-text offsets in the selection to document positions.

        Tracked deletions and field codes occupy document positions but are
        absent from ``content``, so a visible offset does not equal its
        document position. Word exposes no range-scoped revision list, so each
        position is found by binary search on the only monotone quantity Word
        gives us: the length of the visible content from the selection start to
        a candidate position grows by one per visible character and not at all
        across hidden ones.

        That is ``log(n)`` Word reads per offset instead of the one-read-per-
        character scan it replaces, which is what made a tracked-changes
        selection take seconds per suggestion.

        Implementations that cannot answer return None and the caller falls
        back to the guarded write, which refuses rather than misplaces an edit.
        """
        return None

# --- Windows Implementation ---

class WindowsWordIntegration(WordIntegration):
    client: Any # pyright: ignore[reportAny]

    def __init__(self) -> None:
        try:
            import importlib
            self.client = importlib.import_module("win32com.client")
        except ImportError:
            # Mock for non-Windows environments to satisfy linter/runtime
            class MockObject:
                def __getattr__(self, name: str) -> 'MockObject': return MockObject()
                def __call__(self, *args: Any, **kwargs: Any) -> 'MockObject': return MockObject() # pyright: ignore[reportAny]
                def __bool__(self) -> bool: return False
                def __int__(self) -> int: return 0
                def __len__(self) -> int: return 0
                def __add__(self, other: Any) -> int: return 0 # pyright: ignore[reportAny]
                def __sub__(self, other: Any) -> int: return 0 # pyright: ignore[reportAny]
                def __lt__(self, other: Any) -> bool: return False # pyright: ignore[reportAny]
                def __gt__(self, other: Any) -> bool: return False # pyright: ignore[reportAny]
                def __setattr__(self, name: str, value: Any) -> None: pass # pyright: ignore[reportAny]
                def __str__(self) -> str: return ""
            
            class MockClient:
                def GetActiveObject(self, name: str) -> MockObject: return MockObject()
                
            self.client = MockClient()

    def _get_word(self) -> Any: # pyright: ignore[reportAny]
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        return self.client.GetActiveObject("Word.Application") # pyright: ignore[reportAny, reportUnknownMemberType]

    def ensure_ready(self) -> None:
        try:
            word = self._get_word()
            if not word.Documents.Count:
                raise RuntimeError("No active Word document is open.")
        except Exception as e:
            raise RuntimeError(f"Microsoft Word is not running or no document is open: {e}")

    def ensure_track_changes_enabled(self) -> None:
        try:
            word = self._get_word()
            if word.Documents.Count > 0:
                word.ActiveDocument.TrackRevisions = True
        except Exception as e:
            raise RuntimeError(f"Unable to enable Track Changes in Microsoft Word: {e}")

    def apply_live_edit(
        self,
        selection_start: int,
        rel_start: int,
        rel_end: int,
        replacement: str,
        before_text: str | None = None,
        expected_document: str | None = None,
    ) -> tuple[bool, str]:
        """Replace one mapped sub-range on Windows with the same guards.

        Track Changes is suspended for the write (so live edits never record a
        revision) and restored in a ``finally`` block, the range is checked
        before the write, and the result is read back before reporting success.
        """
        if cell_mark_mismatch(before_text, replacement):
            _log_word("live edit refused: it would change the table structure")
            return False, TABLE_STRUCTURE_MESSAGE
        start = selection_start + rel_start
        end = selection_start + rel_end
        try:
            word = self._get_word()
            if not word.Documents.Count:
                return False, "No Word document is open."
            doc = word.ActiveDocument
            if expected_document and str(doc.Name) != expected_document:
                _log_word("live edit refused: active document changed")
                return False, "The active Word document changed — please try again."
            rng = doc.Range(start, end)
            if before_text:
                current = str(rng.Text or "")
                if _normalize_for_compare(current) != _normalize_for_compare(
                    before_text
                ):
                    _log_word("live edit refused: range text changed")
                    return False, "The text moved or changed — please try again."
            old_track = bool(doc.TrackRevisions)
            try:
                doc.TrackRevisions = False
                rng.Text = replacement
            finally:
                doc.TrackRevisions = old_track
            after = str(doc.Range(start, start + len(replacement)).Text or "")
            if _normalize_for_compare(after) == _normalize_for_compare(replacement):
                return True, "Applied."
            _log_word("live edit read-back differs; reporting for review")
            return True, "Applied — please check the document."
        except Exception as exc:
            _log_word(f"live edit failed (Windows): {exc}")
            return False, "Could not apply the edit in Word."

    def ensure_track_changes_disabled(self) -> None:
        try:
            word = self._get_word()
            if word.Documents.Count > 0:
                word.ActiveDocument.TrackRevisions = False
        except Exception as e:
            raise RuntimeError(f"Unable to disable Track Changes in Microsoft Word: {e}")

    def replace_selection_content(self, new_text: str) -> None:
        try:
            word = self._get_word()
            sel = word.Selection
            sel.Text = new_text
        except Exception as e:
            _log_word(f"Error replacing selection content (Windows): {e}")

    def set_selection_range(self, start: int, end: int) -> None:
        try:
            word = self._get_word()
            word.Selection.SetRange(start, end)
        except Exception as e:
            _log_word(f"Error restoring selection range (Windows): {e}")

    def get_selection_info(self) -> tuple[str, int, int, str, str]:
        try:
            word = self._get_word()
            if not word.Documents.Count:
                return "", 0, 0, "", ""
                
            doc = word.ActiveDocument
            sel = word.Selection
            text = sel.Text
            start_pos = sel.Start
            end_pos = sel.End

            if int(sel.StoryType) == WD_COMMENTS_STORY:
                # Comment offsets only make sense inside the comment story;
                # reading the body around them would be nonsense context.
                return str(text), int(start_pos), int(end_pos), "", ""

            before_start = max(0, start_pos - 250)
            if before_start < start_pos:
                context_before = doc.Range(before_start, start_pos).Text
            else:
                context_before = ""
                
            doc_end = doc.Content.End
            after_end = min(doc_end, end_pos + 250)
            if end_pos < after_end:
                context_after = doc.Range(end_pos, after_end).Text
            else:
                context_after = ""
                
            return str(text), int(start_pos), int(end_pos), str(context_before), str(context_after)
        except Exception as e:
            _log_word(f"Error getting text (Windows): {e}")
            return "", 0, 0, "", ""

    def selection_scope(self) -> str:
        """See WordIntegration.selection_scope."""
        try:
            word = self._get_word()
            selection = word.Selection
            if int(selection.StoryType) == WD_COMMENTS_STORY:
                return SCOPE_COMMENTS
            if int(selection.StoryType) != WD_MAIN_TEXT_STORY:
                return SCOPE_OTHER_STORY
            if not selection.Information(WD_WITH_IN_TABLE):
                return SCOPE_MAIN
            # Inside a table: only a *complete* table counts as a table
            # selection. Text inside the cells is edited like any other text.
            if int(selection.Tables.Count) > 0:
                return SCOPE_WHOLE_TABLE
            return SCOPE_MAIN
        except Exception:
            return SCOPE_MAIN

    def replace_comment_selection(
        self,
        expected_text: str,
        new_text: str,
        expected_document: str | None = None,
    ) -> tuple[bool, str]:
        """See WordIntegration.replace_comment_selection (Windows/COM)."""
        try:
            word = self._get_word()
            selection = word.Selection
            if int(selection.StoryType) != WD_COMMENTS_STORY:
                return False, "That selection is no longer inside a comment."
            if expected_text and str(selection.Range.Text) != expected_text:
                return False, "The comment changed — please try again."
            selection.Range.Text = new_text
            if str(selection.Range.Text).rstrip("\r") == new_text.rstrip("\r"):
                return True, "Applied."
            return True, "Applied — please check the comment."
        except Exception as exc:
            _log_word(f"comment edit failed (Windows): {exc}")
            return False, "Could not write to the Word comment."

    def delete_range(self, abs_start: int, abs_end: int) -> None:
        try:
            word = self._get_word()
            doc = word.ActiveDocument
            doc.Range(abs_start, abs_end).Delete()
        except Exception as e:
            _log_word(f"Error deleting range (Windows): {e}")

    def insert_at_position(self, abs_pos: int, text: str) -> None:
        try:
            word = self._get_word()
            doc = word.ActiveDocument
            doc.Range(abs_pos, abs_pos).InsertAfter(text)
        except Exception as e:
            _log_word(f"Error inserting at position (Windows): {e}")

    def replace_range(self, abs_start: int, abs_end: int, text: str) -> None:
        try:
            word = self._get_word()
            doc = word.ActiveDocument
            doc.Range(abs_start, abs_end).Text = text
        except Exception as e:
            _log_word(f"Error replacing range (Windows): {e}")

    def selection_has_fields(self) -> bool:
        try:
            word = self._get_word()
            sel = word.Selection
            return sel.Fields.Count > 0
        except Exception:
            return False

    def get_selection_field_spans(self) -> list[FieldSpan]:
        try:
            word = self._get_word()
            sel = word.Selection
            parsed: list[tuple[int, int, int, int, str]] = []
            for field in sel.Fields:
                try:
                    start = int(field.Range.Start)
                    end = int(field.Range.End)
                    text = str(field.Result.Text or "")
                    parsed.append(
                        (start, end, -1, end, text)
                    )
                except Exception:
                    continue
            spans = [
                FieldSpan(start, end, text)
                for start, end, _result_start, _result_end, text in _drop_nested_fields(
                    parsed
                )
            ]
            return spans
        except Exception as e:
            _log_word(f"Error getting field spans (Windows): {e}")
            return []

    def live_doc_positions(
        self,
        sel_start: int,
        sel_end: int,
        visible_offsets: Sequence[int],
    ) -> dict[int, int] | None:
        """Map visible offsets to document positions by binary search.

        Same invariant as the macOS version (``Range.Text`` omits tracked
        deletions and field codes while positions still count them), but each
        probe is a cheap in-process COM call.
        """
        wanted = sorted({int(offset) for offset in visible_offsets})
        if not wanted:
            return {}
        try:
            word = self._get_word()
            if not word.Documents.Count:
                return None
            doc = word.ActiveDocument
            mapping: dict[int, int] = {}
            for target in wanted:
                lo, hi = int(sel_start), int(sel_end)
                while lo < hi:
                    mid = (lo + hi) // 2
                    if len(str(doc.Range(int(sel_start), mid).Text)) >= target:
                        hi = mid
                    else:
                        lo = mid + 1
                mapping[target] = lo
            return mapping
        except Exception as exc:
            _log_word(f"Error mapping Word selection positions (Windows): {exc}")
            return None

    def get_selection_hidden_spans(
        self,
        start: int = 0,
        end: int = 0,
        exclude_spans: list[tuple[int, int]] | None = None,
        max_hidden: int = 0,
    ) -> list[tuple[int, int]]:
        """Tracked deletions (and moved-from text) inside the selection."""
        try:
            word = self._get_word()
            if not word.Documents.Count:
                return []
            doc = word.ActiveDocument
            sel = word.Selection
            if int(sel.End) <= int(sel.Start):
                return []

            spans: list[tuple[int, int]] = []
            # wdRevisionDelete = 2, wdRevisionMovedFrom = 17,
            # wdRevisionCellDeletion = 20. These hide text from Range.Text.
            hidden_types = (2, 17, 20)
            for rev in doc.Range(int(sel.Start), int(sel.End)).Revisions:
                try:
                    if int(rev.Type) in hidden_types:
                        spans.append(
                            (int(rev.Range.Start), int(rev.Range.End))
                        )
                except Exception:
                    continue
            return spans
        except Exception as e:
            _log_word(f"Error getting hidden spans (Windows): {e}")
            return []

    def read_range_text(self, start: int, end: int) -> str:
        """See WordIntegration.read_range_text (Windows/COM)."""
        try:
            word = self._get_word()
            if not word.Documents.Count:
                return ""
            return str(word.ActiveDocument.Range(int(start), int(end)).Text or "")
        except Exception as exc:
            _log_word(f"could not read the document range: {exc}")
            return ""

    def add_comment(self, comment_text: str) -> None:
        if not comment_text or not comment_text.strip():
            _log_word("Skipping empty comment insertion.")
            return
        try:
            word = self._get_word()
            doc = word.ActiveDocument
            sel = word.Selection
            rng = doc.Range(sel.Start, sel.End)
            doc.Comments.Add(rng, comment_text)
        except Exception as e:
            _log_word(f"Error adding comment (Windows): {e}")


# --- macOS Implementation ---

class MacOSWordIntegration(WordIntegration):
    
    def _run_applescript(
        self, script: str, *args: str, timeout: float | None = None
    ) -> str:
        """
        Executes an AppleScript using `osascript`.
        Uses stdin for the script content to avoid command line length limits.
        Ensures binary mode execution to preserve newlines correctly.

        Every call is bounded: a Word modal dialog would otherwise block the
        caller forever with no way to cancel.
        """
        command = ["osascript", "-"]
        command.extend(args)
        limit = APPLESCRIPT_TIMEOUT_S if timeout is None else timeout

        try:
            completed = subprocess.run(
                command,
                input=script.encode('utf-8'),
                check=True,
                capture_output=True,
                text=False, # Binary mode
                timeout=limit,
            )

            # Decode and handle trailing newline from osascript
            output = completed.stdout.decode('utf-8')
            output = output.removesuffix("\n")
            return output
        except subprocess.TimeoutExpired as exc:
            _log_word(
                f"AppleScript timed out after {limit:.0f}s; "
                "Word is probably showing a dialog"
            )
            raise WordBusyError(
                "Microsoft Word is not responding — close any dialog in Word "
                "and try again."
            ) from exc
        except subprocess.CalledProcessError as e:
            # Re-raise with stderr context decoded
            stdout_str = e.stdout.decode('utf-8') if e.stdout else ""
            stderr_str = e.stderr.decode('utf-8') if e.stderr else ""
            if stderr_str.strip():
                _log_word(f"AppleScript error: {stderr_str.strip()[:300]}")
            raise subprocess.CalledProcessError(e.returncode, e.cmd, output=stdout_str, stderr=stderr_str)

    def apply_live_edit(
        self,
        selection_start: int,
        rel_start: int,
        rel_end: int,
        replacement: str,
        before_text: str | None = None,
        expected_document: str | None = None,
    ) -> tuple[bool, str]:
        """Replace one mapped sub-range of the current selection.

        Live editing must be instant and invisible: Track Changes is
        temporarily suspended around the replacement so the edit never records
        a revision or slows the document down.

        The write is guarded the same way the Accessibility path is:

        * the active document must still be the one the offsets came from,
        * the range must still hold the original text (``before_text``),
        * Track Changes is restored even when the write fails,
        * the result is read back before success is reported.
        """
        from .generic_editing import _debug_log, _mac_set_clipboard

        if cell_mark_mismatch(before_text, replacement):
            _log_word("live edit refused: it would change the table structure")
            return False, TABLE_STRUCTURE_MESSAGE
        start = selection_start + rel_start
        end = selection_start + rel_end
        _mac_set_clipboard(replacement)
        expected_doc = _applescript_quote(expected_document or "")
        expected_text = _applescript_quote(before_text or "")
        script = f"""
        tell application "Microsoft Word"
            if not running then return "NOT_RUNNING"
            if not (exists active document) then return "NO_DOCUMENT"
            if "{expected_doc}" is not "" then
                if (name of active document) is not "{expected_doc}" then return "DOC_CHANGED"
            end if
            set r to create range active document start {start} end {end}
            if "{expected_text}" is not "" then
                if (content of r) is not "{expected_text}" then return "TEXT_CHANGED"
            end if
            set oldTrack to missing value
            try
                set oldTrack to track revisions of active document
                set track revisions of active document to false
            end try
            try
                set content of r to (the clipboard as text)
            on error errMsg number errNum
                try
                    if oldTrack is not missing value then
                        set track revisions of active document to oldTrack
                    end if
                end try
                return "WRITE_FAILED: " & errMsg
            end try
            try
                if oldTrack is not missing value then
                    set track revisions of active document to oldTrack
                end if
            end try
            if (content of r) is (the clipboard as text) then return "OK"
            return "VERIFY_MISMATCH"
        end tell
        """
        try:
            raw = self._run_applescript(script)
        except WordBusyError as exc:
            _log_word(f"live edit blocked: {exc}")
            return False, str(exc)
        except Exception as exc:
            _log_word(f"live edit failed: {exc}")
            return False, str(exc)

        status = _applescript_status(raw)
        if status == "OK":
            return True, "Applied."
        if status.startswith("WRITE_FAILED"):
            _log_word(f"live edit write failed: {status}")
            return False, "Could not write to the Word document."
        if status == "DOC_CHANGED":
            _debug_log("WORD LIVE APPLY: active document changed; refusing")
            return False, "The active Word document changed — please try again."
        if status == "TEXT_CHANGED":
            _debug_log("WORD LIVE APPLY: range no longer holds the original text")
            return False, "The text moved or changed — please try again."
        if status == "VERIFY_MISMATCH":
            # The write landed but Word normalised the text (smart quotes,
            # autocorrect). Report honestly instead of claiming success.
            _log_word("live edit read-back differs; reporting for review")
            return True, "Applied — please check the document."
        if status == "NOT_RUNNING":
            return False, "Microsoft Word is not running."
        if status == "NO_DOCUMENT":
            return False, "No Word document is open."
        if status:
            _log_word(f"live edit unexpected status: {status!r}")
        return False, "Could not apply the edit in Word."

    def set_live_underline(
        self, selection_start: int, rel_start: int, rel_end: int, enable: bool
    ) -> tuple[bool, str]:
        """Apply or restore the pink dot-dot-dash underline on a sub-range.

        The mark is ephemeral: it is applied with Track Changes temporarily
        disabled so Word never records it as a revision, and the original
        underline and colour are captured and restored exactly when cleared.
        """
        start = selection_start + rel_start
        end = selection_start + rel_end
        if enable:
            script = f"""
            tell application "Microsoft Word"
                set oldTrack to missing value
                try
                    set oldTrack to track revisions of active document
                    set track revisions of active document to false
                end try
                try
                    set r to create range active document start {start} end {end}
                    set origUnderline to underline of font object of r
                    set origColor to color of font object of r
                    set underline of font object of r to underline dot dot dash
                    set color of font object of r to {{58082, 14906, 23387}}
                on error errMsg number errNum
                    try
                        if oldTrack is not missing value then
                            set track revisions of active document to oldTrack
                        end if
                    end try
                    error errMsg number errNum
                end try
                try
                    if oldTrack is not missing value then
                        set track revisions of active document to oldTrack
                    end if
                end try
                return (origUnderline as text) & "||" & ((item 1 of origColor) as text) & "," & ((item 2 of origColor) as text) & "," & ((item 3 of origColor) as text)
            end tell
            """
        else:
            original = None
            for entry in getattr(self, "_live_underline_ranges", []):
                if entry.get("start") == start and entry.get("end") == end:
                    original = entry
                    break
            self._restore_underline(start, end, original)
            self._live_underline_ranges = [
                entry
                for entry in getattr(self, "_live_underline_ranges", [])
                if not (entry.get("start") == start and entry.get("end") == end)
            ]
            return True, "Applied."
        try:
            output = self._run_applescript(script)
            if enable:
                if not hasattr(self, "_live_underline_ranges"):
                    self._live_underline_ranges: list[dict[str, Any]] = []
                underline = "underline none"
                color: tuple[int, int, int] | None = None
                try:
                    parts = (output or "").split("||", 1)
                    if len(parts) == 2 and parts[0].strip():
                        underline = parts[0].strip()
                    if len(parts) == 2:
                        channels = [int(x) for x in parts[1].split(",")[:3]]
                        if len(channels) == 3:
                            color = (channels[0], channels[1], channels[2])
                except (ValueError, IndexError):
                    pass
                self._live_underline_ranges.append(
                    {
                        "start": start,
                        "end": end,
                        "underline": underline,
                        "color": color,
                    }
                )
            return True, "Applied."
        except Exception as exc:
            return False, str(exc)

    def _restore_underline(
        self, start: int, end: int, original: dict[str, Any] | None
    ) -> None:
        underline = (original or {}).get("underline") or "underline none"
        color = (original or {}).get("color") or (0, 0, 0)
        script = f"""
        tell application "Microsoft Word"
            set oldTrack to missing value
            try
                set oldTrack to track revisions of active document
                set track revisions of active document to false
            end try
            try
                set r to create range active document start {start} end {end}
                set underline of font object of r to {underline}
                set color of font object of r to {{{color[0]}, {color[1]}, {color[2]}}}
            on error errMsg number errNum
                try
                    if oldTrack is not missing value then
                        set track revisions of active document to oldTrack
                    end if
                end try
                error errMsg number errNum
            end try
            try
                if oldTrack is not missing value then
                    set track revisions of active document to oldTrack
                end if
            end try
        end tell
        """
        self._run_applescript(script)

    def clear_live_underlines(self) -> None:
        """Revert every underline this integration applied."""
        ranges = list(getattr(self, "_live_underline_ranges", []))
        self._live_underline_ranges = []
        for entry in ranges:
            self._restore_underline(entry["start"], entry["end"], entry)

    def active_document_name(self) -> str:
        try:
            return self._run_applescript(
                'tell application "Microsoft Word" to get name of active document'
            ).strip()
        except Exception:
            return ""

    def live_marks(self) -> list[dict[str, Any]]:
        """Return the currently applied live marks with their originals."""
        return [
            dict(entry)
            for entry in getattr(self, "_live_underline_ranges", [])
        ]

    def restore_live_mark(
        self, start: int, end: int, original: dict[str, Any]
    ) -> None:
        self._restore_underline(start, end, original)

    def ensure_ready(self) -> None:
        script = """
        tell application "Microsoft Word"
            if not running then error "Microsoft Word is not running."
            if not (exists active document) then error "No active Word document is open."
        end tell
        """
        self._run_applescript(script)

    def ensure_track_changes_enabled(self) -> None:
        scripts = [
            """
            tell application "Microsoft Word"
                set track revisions of active document to true
            end tell
            """,
            """
            tell application "Microsoft Word"
                set track changes of active document to true
            end tell
            """
        ]
        last_error = None
        for script in scripts:
            try:
                self._run_applescript(script)
                return
            except Exception as exc:
                last_error = exc
        if last_error:
            raise RuntimeError("Unable to enable Track Changes in Microsoft Word.") from last_error

    def ensure_track_changes_disabled(self) -> None:
        scripts = [
            """
            tell application "Microsoft Word"
                set track revisions of active document to false
            end tell
            """,
            """
            tell application "Microsoft Word"
                set track changes of active document to false
            end tell
            """
        ]
        last_error = None
        for script in scripts:
            try:
                self._run_applescript(script)
                return
            except Exception as exc:
                last_error = exc
        if last_error:
            raise RuntimeError("Unable to disable Track Changes in Microsoft Word.") from last_error

    def get_selection_info(self) -> tuple[str, int, int, str, str]:
        """Read the current selection (short timeout: this runs every poll)."""
        script = """
        tell application "Microsoft Word"
            if not (exists active document) then error "No active Word document is open."
            
            -- Get selection
            set mySelection to selection
            set myRange to text object of mySelection
            
            -- Get selection content and start position (0-based).
            set startPos to start of content of myRange
            set myContent to content of myRange

            -- CRITICAL: Use the real range end. Word's internal character
            -- positions include hidden field code characters (e.g. EndNote
            -- citations), so startPos + (length of myContent) is too small
            -- whenever the selection contains a field.
            set endPos to end of content of myRange
            
            -- Comment text lives in its own story: its offsets point into the
            -- comment, not the manuscript, so reading the body around them
            -- would hand the model unrelated text.
            set inComment to false
            try
                set inComment to ((get range information myRange information type in comment pane) as text) is "true"
            end try

            -- Context Before (approx 30 words -> ~250 chars)
            set contextBefore to ""
            if startPos > 0 and not inComment then
                set beforeStart to startPos - 250
                if beforeStart < 0 then set beforeStart to 0
                set rangeBefore to create range active document start beforeStart end startPos
                set contextBefore to content of rangeBefore
            end if
            
            -- Context After
            set docRange to text object of active document
            set docEnd to end of content of docRange
            
            set contextAfter to ""
            if endPos < docEnd and not inComment then
                set afterEnd to endPos + 250
                if afterEnd > docEnd then set afterEnd to docEnd
                set rangeAfter to create range active document start endPos end afterEnd
                set contextAfter to content of rangeAfter
            end if
            
            -- Use a unique separator to avoid issues with pipe in text
            return (startPos as string) & "###PROOF_SEP###" & myContent & "###PROOF_SEP###" & (endPos as string) & "###PROOF_SEP###" & contextBefore & "###PROOF_SEP###" & contextAfter
        end tell
        """
        try:
            result = self._run_applescript(
                script, timeout=WORD_POLL_READ_TIMEOUT_S
            )
            if "###PROOF_SEP###" in result:
                parts = result.split("###PROOF_SEP###")
                if len(parts) >= 5:
                    text = parts[1]
                    # A collapsed selection comes back as the literal string
                    # "missing value" from AppleScript; treat it as empty so
                    # the live service never previews it.
                    if str(text).strip().lower() == "missing value":
                        text = ""
                    return text, int(parts[0]), int(parts[2]), parts[3], parts[4]
                if len(parts) >= 4:
                    return parts[1], int(parts[0]), 0, parts[2], parts[3]
                if len(parts) >= 2:
                    return parts[1], int(parts[0]), 0, "", ""
        except Exception as e:
            _log_word(f"Error getting text with context: {e}")
            
        return "", 0, 0, "", ""

    def selection_scope(self) -> str:
        """See WordIntegration.selection_scope.

        One AppleScript call, made once per new selection (not on every poll).
        Every check is a Word information flag rather than a localised story
        name, so the answer does not depend on the Word UI language. Any
        failure answers SCOPE_MAIN, which keeps the previous behaviour.
        """
        script = """
        try
            tell application "Microsoft Word"
                if not (exists active document) then return "main"
                set myRange to text object of selection

                set inComment to "false"
                try
                    set inComment to ((get range information myRange information type in comment pane) as text)
                end try
                if inComment is "true" then return "comments"

                set elsewhere to "false"
                try
                    set elsewhere to ((get range information myRange information type in header footer) as text)
                end try
                if elsewhere is "true" then return "other_story"
                set elsewhere to "false"
                try
                    set elsewhere to ((get range information myRange information type in footnote endnote pane) as text)
                end try
                if elsewhere is "true" then return "other_story"
                set elsewhere to "false"
                try
                    set elsewhere to ((get range information myRange information type in footnote) as text)
                end try
                if elsewhere is "true" then return "other_story"
                set elsewhere to "false"
                try
                    set elsewhere to ((get range information myRange information type in endnote) as text)
                end try
                if elsewhere is "true" then return "other_story"

                -- Inside a table the selection is only refused when a WHOLE
                -- table is covered (Word's table handle, Select Table, or a
                -- Cmd+A in the table). Cell text stays editable.
                set tableList to tables of myRange
                if (count of tableList) > 0 then
                    set selStart to start of content of myRange
                    set selEnd to end of content of myRange
                    repeat with aTable in tableList
                        set tableRange to text object of aTable
                        if selStart ≤ (start of content of tableRange) and selEnd ≥ (end of content of tableRange) then
                            return "whole_table"
                        end if
                    end repeat
                end if
                return "main"
            end tell
        on error errMsg
            return "main"
        end try
        """
        try:
            res = self._run_applescript(script).strip()
        except Exception as e:
            _log_word(f"Error checking the selection scope: {e}")
            return SCOPE_MAIN
        if res in (SCOPE_COMMENTS, SCOPE_WHOLE_TABLE, SCOPE_OTHER_STORY):
            return res
        return SCOPE_MAIN

    def replace_comment_selection(
        self,
        expected_text: str,
        new_text: str,
        expected_document: str | None = None,
    ) -> tuple[bool, str]:
        """Rewrite the text of the comment the selection is inside.

        Comment text has no writable sub-ranges through AppleScript (the range
        returned by ``set range`` there cannot even be read back), so the whole
        selection is replaced with the corrected text the caller built from it.
        The write is guarded like the document path: the same document, the
        same comment text, Track Changes restored, and the result read back.
        """
        import tempfile

        from .generic_editing import _debug_log

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(new_text.replace("\r", "\n"))
            tmp_path = tmp.name
        expected_doc = _applescript_quote(expected_document or "")
        expected = _applescript_quote(expected_text or "")
        script = f"""
        on run argv
            set filePath to item 1 of argv
            try
                set newContent to read (filePath as POSIX file)
            on error
                return "ERROR_READ_FAILED"
            end try
            try
                tell application "Microsoft Word"
                    if not running then return "NOT_RUNNING"
                    if not (exists active document) then return "NO_DOCUMENT"
                    if "{expected_doc}" is not "" then
                        if (name of active document) is not "{expected_doc}" then return "DOC_CHANGED"
                    end if
                    set inComment to "false"
                    try
                        set inComment to ((get range information (text object of selection) information type in comment pane) as text)
                    end try
                    if inComment is not "true" then return "NOT_COMMENT"
                    if "{expected}" is not "" then
                        if (content of (text object of selection)) is not "{expected}" then return "TEXT_CHANGED"
                    end if
                    set oldTrack to missing value
                    try
                        set oldTrack to track revisions of active document
                        set track revisions of active document to false
                    end try
                    try
                        set content of selection to newContent
                    on error errMsg number errNum
                        try
                            if oldTrack is not missing value then
                                set track revisions of active document to oldTrack
                            end if
                        end try
                        return "WRITE_FAILED: " & errMsg
                    end try
                    try
                        if oldTrack is not missing value then
                            set track revisions of active document to oldTrack
                        end if
                    end try
                    if (content of (text object of selection)) is newContent then return "OK"
                    return "VERIFY_MISMATCH"
                end tell
            on error errMsg
                return "ERROR:" & errMsg
            end try
        end run
        """
        try:
            raw = self._run_applescript(script, tmp_path)
        except WordBusyError as exc:
            _log_word(f"comment edit blocked: {exc}")
            return False, str(exc)
        except Exception as exc:
            _log_word(f"comment edit failed: {exc}")
            return False, "Could not write to the Word comment."
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        status = _applescript_status(raw)
        if status == "OK":
            return True, "Applied."
        if status == "TEXT_CHANGED":
            _debug_log("WORD COMMENT: the comment changed before the write")
            return False, "The comment changed — please try again."
        if status == "DOC_CHANGED":
            _debug_log("WORD COMMENT: active document changed; refusing")
            return False, "The active Word document changed — please try again."
        if status == "NOT_COMMENT":
            _debug_log("WORD COMMENT: the selection left the comment; refusing")
            return False, "Select the comment text again to apply this change."
        if status == "NOT_RUNNING":
            return False, "Microsoft Word is not running."
        if status == "NO_DOCUMENT":
            return False, "No Word document is open."
        if status == "VERIFY_MISMATCH":
            _log_word("comment edit read-back differs; reporting for review")
            return True, "Applied — please check the comment."
        if status.startswith("WRITE_FAILED"):
            _log_word(f"comment edit write failed: {status}")
            return False, "Could not write to the Word comment."
        _log_word(f"comment edit unexpected status: {status!r}")
        return False, "Could not apply the edit in the comment."
            
    def delete_range(self, abs_start: int, abs_end: int) -> None:
        script = """
        on run argv
            set absStart to (item 1 of argv) as integer
            set absEnd to (item 2 of argv) as integer
            try
                tell application "Microsoft Word"
                    set theRange to create range active document start absStart end absEnd
                    delete theRange
                end tell
            on error errMsg number errNum
                if errNum is -1728 then
                    return "ERROR_OBJECT_NOT_FOUND"
                else if errNum is -10006 then
                    return "ERROR_WRITE_DENIED"
                else
                    error errMsg number errNum
                end if
            end try
        end run
        """
        res = self._run_applescript(script, str(abs_start), str(abs_end))
        self._handle_script_error(res, f"delete range {abs_start}-{abs_end}")

    def insert_at_position(self, abs_pos: int, text: str) -> None:
        script = """
        on run argv
            set absPos to (item 1 of argv) as integer
            set newText to item 2 of argv
            try
                tell application "Microsoft Word"
                    set docEnd to (end of content of text object of active document) - 1
                    if absPos >= docEnd then
                        set theRange to create range active document start docEnd end docEnd
                        set content of theRange to (content of theRange) & newText
                    else
                        set theRange to create range active document start absPos end (absPos + 1)
                        set existingChar to content of theRange
                        if existingChar is missing value then set existingChar to ""
                        set content of theRange to newText & existingChar
                    end if
                end tell
            on error errMsg number errNum
                if errNum is -1728 then
                    return "ERROR_OBJECT_NOT_FOUND"
                else if errNum is -10006 then
                    return "ERROR_WRITE_DENIED"
                else
                    error errMsg number errNum
                end if
            end try
        end run
        """
        res = self._run_applescript(script, str(abs_pos), text)
        self._handle_script_error(res, f"insert at {abs_pos}")

    def replace_range(self, abs_start: int, abs_end: int, text: str) -> None:
        script = """
        on run argv
            set absStart to (item 1 of argv) as integer
            set absEnd to (item 2 of argv) as integer
            set newText to item 3 of argv
            try
                tell application "Microsoft Word"
                    set theRange to create range active document start absStart end absEnd
                    set content of theRange to newText
                end tell
            on error errMsg number errNum
                if errNum is -1728 then
                    return "ERROR_OBJECT_NOT_FOUND"
                else if errNum is -10006 then
                    return "ERROR_WRITE_DENIED"
                else
                    error errMsg number errNum
                end if
            end try
        end run
        """
        res = self._run_applescript(script, str(abs_start), str(abs_end), text)
        self._handle_script_error(res, f"replace range {abs_start}-{abs_end}")

    def replace_selection_content(self, new_text: str) -> None:
        safe_text = new_text.replace("\r", "\n")
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(safe_text)
            tmp_path = tmp.name
        try:
            script = """
            on run argv
                set filePath to item 1 of argv
                try
                    set newContent to read (filePath as POSIX file)
                on error
                    return "ERROR_READ_FAILED"
                end try
                try
                    tell application "Microsoft Word"
                        set content of text object of selection to newContent
                    end tell
                    return "OK"
                on error errMsg number errNum
                    return "ERROR:" & errMsg & "(" & (errNum as string) & ")"
                end try
            end run
            """
            res = self._run_applescript(script, tmp_path)
            self._handle_script_error(res, "replace selection content")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def set_selection_range(self, start: int, end: int) -> None:
        script = """
        on run argv
            set selStart to (item 1 of argv) as integer
            set selEnd to (item 2 of argv) as integer
            try
                tell application "Microsoft Word"
                    if not (exists active document) then return ""
                    set myRange to create range active document start selStart end selEnd
                    select myRange
                end tell
            on error
                return ""
            end try
        end run
        """
        try:
            self._run_applescript(script, str(start), str(end))
        except Exception as e:
            _log_word(f"Error restoring selection range (macOS): {e}")

    def selection_has_fields(self) -> bool:
        script = """
        on run
            try
                tell application "Microsoft Word"
                    set fieldCount to (count of fields of text object of selection)
                    return (fieldCount as string)
                end tell
            on error
                return "0"
            end try
        end run
        """
        try:
            res = self._run_applescript(script)
            return int(res.strip()) > 0
        except Exception:
            return False

    def get_selection_field_spans(self) -> list[FieldSpan]:
        """Return fields in the current selection on macOS.

        Word exposes each field's ``result range``, which reports the exact
        visible result text and its document range. Use that as the primary
        source; it avoids DisplayText parsing and the character-by-character
        scan that previously caused position drift on tracked-change text.
        The bounded scan remains only as a fallback for fields whose result
        range cannot be materialized.
        """
        list_script = """
        on run
            try
                tell application "Microsoft Word"
                    if not (exists active document) then return ""
                    set fs to fields of text object of selection
                    set out to ""
                    repeat with i from 1 to (count of fs)
                        set f to item i of fs
                        set codeStart to -1
                        set codeEnd to -1
                        try
                            set codeStart to start of content of field code of f
                            set codeEnd to end of content of field code of f
                        end try
                        set resultStart to -1
                        set resultEnd to -1
                        set resultText to ""
                        try
                            set rr to result range of f
                            set rt to content of rr
                            if rt is not missing value then
                                set resultText to rt
                                set resultStart to start of content of rr
                                set resultEnd to end of content of rr
                            end if
                        end try
                        set out to out & (codeStart as string) & "###FIELD_SPAN###" & (codeEnd as string) & "###FIELD_SPAN###" & (resultStart as string) & "###FIELD_SPAN###" & (resultEnd as string) & "###FIELD_SPAN###" & resultText & "###FIELD_END###"
                    end repeat
                    return out
                end tell
            on error
                return ""
            end try
        end run
        """
        try:
            raw = self._run_applescript(list_script)
        except Exception as e:
            _log_word(f"Error listing Word fields (macOS): {e}")
            return []

        parsed: list[tuple[int, int, int, int, str]] = []
        for chunk in raw.split("###FIELD_END###"):
            if "###FIELD_SPAN###" not in chunk:
                continue
            parts = chunk.split("###FIELD_SPAN###")
            if len(parts) < 5:
                continue
            try:
                code_start = int(parts[0].strip())
                code_end = int(parts[1].strip())
                result_start = int(parts[2].strip())
                result_end = int(parts[3].strip())
            except ValueError:
                continue
            result_text = parts[4]
            if code_start <= 0 or code_end <= 0:
                continue
            parsed.append(
                (code_start, code_end, result_start, result_end, result_text)
            )

        spans: list[FieldSpan] = []
        for code_start, code_end, result_start, result_end, result_text in (
            _drop_nested_fields(parsed)
        ):
            if code_start <= 0 or result_start <= 0 or result_end <= result_start:
                # Unusual field Word couldn't fully describe. Fall back to the
                # bounded scan so the rest of the selection still maps.
                if code_end > 0:
                    result_text, result_end = self._mac_scan_field_result(code_end + 1)
                else:
                    result_text = ""
            spans.append(
                FieldSpan(
                    max(0, code_start - 1),
                    result_end + 1,
                    result_text,
                )
            )
        return spans

    def live_doc_positions(
        self,
        sel_start: int,
        sel_end: int,
        visible_offsets: Sequence[int],
    ) -> dict[int, int] | None:
        """Map visible offsets to document positions with one AppleScript.

        Each offset is found by binary search over the candidate document
        positions, comparing ``length of content`` from the selection start.
        Word omits tracked deletions and field codes from ``content`` but still
        counts them in positions, so that length is the number of visible
        characters before the candidate: monotone, and exactly what a search
        needs. Ten-odd reads per offset replace the previous one-read-per-
        character scan, which took seconds on a tracked-changes selection.
        """
        wanted = sorted({int(offset) for offset in visible_offsets})
        if not wanted:
            return {}
        script = """
        on run argv
            set aStart to (item 1 of argv) as integer
            set aEnd to (item 2 of argv) as integer
            set out to ""
            try
                tell application "Microsoft Word"
                    if not (exists active document) then return ""
                    set d to active document
                    repeat with i from 3 to (count of argv)
                        set target to (item i of argv) as integer
                        set lo to aStart
                        set hi to aEnd
                        repeat while lo < hi
                            set mid to (lo + hi) div 2
                            set r to create range d start aStart end mid
                            if (length of (content of r as string)) >= target then
                                set hi to mid
                            else
                                set lo to mid + 1
                            end if
                        end repeat
                        set out to out & (lo as string) & "###POS###"
                    end repeat
                end tell
            on error
                return ""
            end try
            return out
        end run
        """
        args = [str(sel_start), str(sel_end)] + [str(offset) for offset in wanted]
        try:
            raw = self._run_applescript(script, *args, timeout=WORD_MAP_TIMEOUT_S)
        except Exception as exc:
            _log_word(f"Error mapping Word selection positions: {exc}")
            return None
        parts = [part.strip() for part in raw.split("###POS###") if part.strip()]
        if len(parts) != len(wanted):
            _log_word(
                "Word position mapping returned "
                f"{len(parts)} of {len(wanted)} offsets"
            )
            return None
        mapping: dict[int, int] = {}
        try:
            for offset, position in zip(wanted, parts, strict=True):
                mapping[offset] = int(position)
        except ValueError:
            return None
        return mapping

    def get_selection_hidden_spans(
        self,
        start: int = 0,
        end: int = 0,
        exclude_spans: list[tuple[int, int]] | None = None,
        max_hidden: int = 0,
    ) -> list[tuple[int, int]]:
        """Return tracked-deletion spans inside the current selection.

        Word does not expose a range-scoped revision collection through
        AppleScript, but it does omit deleted characters from ``content``
        while still counting them in document positions. A one-pass character
        scan over the selection locates those missing positions without ever
        touching revisions elsewhere in a long document. Field spans are
        excluded because field-code characters are hidden for a different
        reason and are already mapped separately.
        """
        exclude_spans = exclude_spans or []
        exclude_args: list[str] = []
        for span_start, span_end in exclude_spans:
            exclude_args.extend((str(span_start), str(span_end)))

        script = """
        on run argv
            set aStart to (item 1 of argv) as integer
            set aEnd to (item 2 of argv) as integer
            set maxHidden to (item 3 of argv) as integer
            set out to ""
            set argCount to count of argv
            set foundCount to 0
            try
                tell application "Microsoft Word"
                    if not (exists active document) then return ""
                    set d to active document
                    set runStart to -1
                    repeat with p from aStart to (aEnd - 1)
                        set excluded to false
                        repeat with i from 4 to argCount by 2
                            if p >= (item i of argv as integer) and p < (item (i + 1) of argv as integer) then
                                set excluded to true
                                exit repeat
                            end if
                        end repeat
                        if not excluded then
                            set r to create range d start p end (p + 1)
                            set c to content of r
                            if c is missing value then
                                if runStart is -1 then set runStart to p
                                set foundCount to foundCount + 1
                            else
                                if runStart is not -1 then
                                    set out to out & (runStart as string) & "###HSPAN###" & (p as string) & "###HSEND###"
                                    set runStart to -1
                                end if
                            end if
                        else
                            if runStart is not -1 then
                                set out to out & (runStart as string) & "###HSPAN###" & (p as string) & "###HSEND###"
                                set runStart to -1
                            end if
                        end if
                        if maxHidden > 0 and foundCount >= maxHidden then
                            if runStart is not -1 then
                                set out to out & (runStart as string) & "###HSPAN###" & ((p + 1) as string) & "###HSEND###"
                                set runStart to -1
                            end if
                            exit repeat
                        end if
                    end repeat
                    if runStart is not -1 then
                        set out to out & (runStart as string) & "###HSPAN###" & (aEnd as string) & "###HSEND###"
                    end if
                    return out
                end tell
            on error
                return ""
            end try
        end run
        """
        args = [str(start), str(end), str(max_hidden)] + exclude_args
        try:
            raw = self._run_applescript(script, *args)
        except Exception as e:
            _log_word(f"Error scanning Word selection (macOS): {e}")
            return []

        spans: list[tuple[int, int]] = []
        for chunk in raw.split("###HSEND###"):
            if "###HSPAN###" not in chunk:
                continue
            parts = chunk.split("###HSPAN###")
            if len(parts) < 2:
                continue
            try:
                spans.append((int(parts[0].strip()), int(parts[1].strip())))
            except ValueError:
                continue
        return spans

    def _mac_read_range_text(self, start: int, end: int) -> str:
        script = """
        on run argv
            set aStart to (item 1 of argv) as integer
            set aEnd to (item 2 of argv) as integer
            try
                tell application "Microsoft Word"
                    set r to create range active document start aStart end aEnd
                    return content of r
                end tell
            on error
                return ""
            end try
        end run
        """
        try:
            return self._run_applescript(script, str(start), str(end))
        except Exception:
            return ""

    def _mac_scan_field_result(self, result_start: int) -> tuple[str, int]:
        script = f"""
        on run argv
            set resultStart to (item 1 of argv) as integer
            try
                tell application "Microsoft Word"
                    set out to ""
                    set p to resultStart
                    repeat {FIELD_RESULT_SCAN_LIMIT} times
                        set r to create range active document start p end (p + 1)
                        set c to ""
                        try
                            set c to content of r
                        on error
                            set c to missing value
                        end try
                        if c is missing value or c is "" then
                            return "###SCAN###" & out & "###SCAN###" & (p as string)
                        end if
                        set out to out & c
                        set p to p + 1
                    end repeat
                    return "###SCAN###" & out & "###SCAN###" & (p as string)
                end tell
            on error
                return ""
            end try
        end run
        """
        try:
            res = self._run_applescript(script, str(result_start))
        except Exception:
            return "", result_start
        if "###SCAN###" in res:
            parts = res.split("###SCAN###")
            if len(parts) >= 3:
                try:
                    return parts[1], int(parts[2])
                except ValueError:
                    pass
        return "", result_start

    @staticmethod
    def _field_result_from_code(code_text: str) -> str | None:
        """Extract the visible result text from EndNote/Zotero field code."""
        if not code_text:
            return None
        match = re.search(r"<DisplayText>(.*?)</DisplayText>", code_text, re.DOTALL)
        if match:
            return html.unescape(match.group(1))
        for key in ("formattedCitation", "plainCitation"):
            match = re.search(
                re.escape(key) + r'"\s*:\s*("(?:\\.|[^"\\])*")',
                code_text,
                re.DOTALL,
            )
            if match:
                try:
                    return json.loads(match.group(1))
                except Exception:
                    continue
        return None

    def read_range_text(self, start: int, end: int) -> str:
        """See WordIntegration.read_range_text (macOS/AppleScript).

        Returned as Word reports it: this becomes the before-text the undo
        checks against, so it has to be in Word's own spelling rather than the
        spelling that was sent to it. An empty range comes back as the literal
        "missing value", which is normalised to an empty string the same way
        the selection read does it.
        """
        text = self._mac_read_range_text(int(start), int(end))
        if str(text).strip().lower() == "missing value":
            return ""
        return text

    def add_comment(self, comment_text: str) -> None:
        import subprocess as sp

        if not comment_text or not comment_text.strip():
            _log_word("Skipping empty comment insertion.")
            return

        saved_clipboard = None
        try:
            try:
                result = sp.run(["pbpaste"], capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    saved_clipboard = result.stdout
            except Exception:
                pass

            sp.run(["pbcopy"], input=comment_text.encode("utf-8"), check=True)
            self._trigger_comment_and_paste(comment_text)
        except Exception as e:
            # The note is deliberately left on the clipboard: Word would not
            # take it, and the caller tells the user where to find it.
            _log_word(
                f"Comment insertion failed: {e} "
                "(the comment text is still on the clipboard)"
            )
            raise
        if saved_clipboard is not None:
            try:
                sp.run(
                    ["pbcopy"],
                    input=saved_clipboard.encode("utf-8"),
                    check=True,
                )
            except Exception:
                pass

    def _comment_count(self) -> int | None:
        """Number of comments in the active document, or None if unknown."""
        try:
            raw = self._run_applescript(
                'tell application "Microsoft Word" to return '
                "(count of comments of active document) as text",
                timeout=APPLESCRIPT_READ_TIMEOUT_S,
            )
            return int(str(raw).strip())
        except Exception:
            return None

    def _trigger_comment_and_paste(self, comment_text: str) -> None:
        """Open Word's comment box, type the comment into it, and post it.

        Word for Mac has no AppleScript way to create a comment, so the box is
        opened the way a reader would: Insert ▸ Comment, with the Review
        ribbon's New Comment button as the second door for builds that ignore
        the menu item.

        The box Word opens is a *draft*: ``count of comments`` stays exactly
        as it was until the draft is posted with Cmd+Return. Waiting on the
        count therefore ended the flow while the box sat open and empty — the
        text was never typed, and the document gained nothing. The trigger now
        waits for the box itself, pastes into it, posts it, and only uses the
        count afterwards to confirm that a comment really landed.
        """
        last_error = "unknown"
        before_comments = self._comment_count()
        pid = _word_pid()
        methods: list[tuple[str, Callable[[], bool | None]]] = [
            ("menu_insert", self._trigger_insert_comment_menu),
            (
                "review_ribbon",
                lambda: _press_new_comment_button(pid) if pid else False,
            ),
        ]

        # A box that is already open is used as it is. Word's Insert ▸ Comment
        # toggles the draft: pressing it while a box is open cancels that box,
        # so triggering blindly threw away a box that was ready for the text.
        opened = bool(pid is not None and _comment_box_open(pid))
        if opened:
            _log_word("  Comment box was already open; using it")

        for method_name, trigger in ([] if opened else methods):
            try:
                sent = trigger()
            except Exception as ex:
                last_error = f"{method_name}: {ex}"
                _log_word(f"  Comment trigger '{method_name}' failed: {ex}")
                continue
            if sent is False:
                last_error = f"{method_name}: the command is not on screen"
                _log_word(
                    f"  Comment trigger '{method_name}' not available"
                )
                continue
            _log_word(f"  Comment trigger '{method_name}' sent")
            if self._wait_for_comment_box(before_comments, pid):
                _log_word(f"  Comment box open after '{method_name}'")
                opened = True
                break
            last_error = f"{method_name}: Word did not open a comment box"
            _log_word(f"  Comment method '{method_name}' left no comment box")

        if not opened:
            raise RuntimeError(
                "Word did not open a comment box, so the comment text was not "
                "inserted (it would otherwise be typed into the document). "
                f"Last error: {last_error}"
            )

        # Fill the box: it is given focus first, the note is pasted into it,
        # and the text is read back before Word posts it.
        if pid is not None and _fill_comment_box(pid, comment_text):
            if self._wait_for_comment_posted(before_comments, comment_text, pid):
                return
            _log_word("  Comment box was filled but the comment did not post")

        # Fallback: paste and post with Cmd+Return (Word's own "Post comment"),
        # but only while the box itself holds the keyboard focus. That is the
        # one state in which the text cannot land in the manuscript.
        if pid is not None and _comment_box_has_focus(pid):
            paste_script = '''
                delay 0.3
                tell application "System Events"
                    tell process "Microsoft Word"
                        keystroke "v" using {command down}
                        delay 0.3
                        keystroke return using {command down}
                    end tell
                end tell
                return "OK"
            '''
            self._run_applescript(paste_script)
            if self._wait_for_comment_posted(before_comments, comment_text, pid):
                return

        raise RuntimeError(
            "Word's comment box was open but the comment did not appear in "
            "the document; the comment text is on your clipboard."
        )

    def _trigger_insert_comment_menu(self) -> bool:
        """Insert ▸ Comment through Word's own menu bar."""
        script = '''
            tell application "Microsoft Word" to activate
            delay 0.3
            tell application "System Events"
                tell process "Microsoft Word"
                    set frontmost to true
                    delay 0.15
                    click menu item "Comment" of menu "Insert" of menu bar 1
                end tell
            end tell
            return "OK"
        '''
        result = self._run_applescript(script)
        return not (result and "ERROR" in result)

    def _wait_for_comment_box(
        self, before_comments: int | None, pid: int | None
    ) -> bool:
        """Wait briefly for Word's comment box to appear.

        Either signal counts: a comment that joined the document straight away
        (older builds post on the spot) or the draft composer current builds
        show before the comment exists. Both mean the paste will land in the
        comment rather than in the manuscript.
        """
        deadline = time.monotonic() + COMMENT_BOX_TIMEOUT_S
        while True:
            # The Accessibility read is a local call; the count is an osascript
            # round trip, so it is only asked once per pass.
            if pid is not None and _comment_box_open(pid):
                return True
            now = self._comment_count()
            if before_comments is not None and now is not None:
                if now > before_comments:
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(COMMENT_BOX_POLL_S)

    def _wait_for_comment_posted(
        self,
        before_comments: int | None,
        comment_text: str,
        pid: int | None,
    ) -> bool:
        """Wait until the note is visibly a posted comment.

        Word's modern comments never appear in ``count of comments`` (that is
        what the old guard tripped over), so the pane's own card for the note
        is the primary evidence; the count is accepted as well for Word
        builds whose comments AppleScript can still see.
        """
        deadline = time.monotonic() + COMMENT_POST_TIMEOUT_S
        while True:
            now = self._comment_count()
            if (
                before_comments is not None
                and now is not None
                and now > before_comments
            ):
                return True
            if pid is not None and _comment_posted_in_pane(pid, comment_text):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(COMMENT_POST_POLL_S)

    def _handle_script_error(self, res: str, context: str):
        if res:
            res = res.strip()
            if res == "ERROR_OBJECT_NOT_FOUND":
                _log_word(f"  ! Warning: Could not {context} (Object not found). Skipping.")
            elif res == "ERROR_WRITE_DENIED":
                _log_word(f"  ! Warning: Write denied for {context}. Skipping.")


# --- Factory ---

def get_word_integration() -> WordIntegration:
    if platform.system() == "Windows":
        return WindowsWordIntegration()
    else:
        return MacOSWordIntegration()
