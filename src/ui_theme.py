"""Shared design tokens and the word-level diff renderer.

The suggestion panel and the main window's review view both draw on these
so the two surfaces stay visually consistent without duplicated styles.
"""

import difflib
import html
import re

# Shared palette (cool Google-inspired neutrals used by the live panel).
BLUE_PRIMARY = "#1A73E8"
BLUE_HOVER = "#1765CC"
BLUE_PRESSED = "#1256A8"
TEXT_PRIMARY = "#202124"
TEXT_MUTED = "#5F6368"
TEXT_ARROW = "#9AA0A6"
BORDER_SOFT = "#E3E6EB"
DIVIDER = "#F1F3F5"
DIFF_WELL = "#F7F8FA"
DIFF_WELL_BORDER = "#EDF0F4"
RED_DELETE = "#C5221F"
RED_DELETE_BG = "#FCE8E6"
GREEN_INSERT = "#137333"
GREEN_INSERT_BG = "#E6F4EA"

OLD_STYLE = f"color:{RED_DELETE}; background:{RED_DELETE_BG};"
NEW_STYLE = (
    f"color:{GREEN_INSERT}; background:{GREEN_INSERT_BG}; font-weight:500;"
)
ARROW_STYLE = f"color:{TEXT_ARROW};"
CONTEXT_STYLE = f"color:{TEXT_MUTED};"

DOT_RED = "#D93025"
DOT_AMBER = "#F9AB00"
DOT_BLUE = BLUE_PRIMARY


def reason_color(reason: str) -> str:
    """Colour for a suggestion's reason dot.

    Spelling/capitalisation errors are red, grammar/punctuation amber, and
    everything else (word choice, style, clarity) blue.
    """
    r = (reason or "").lower()
    if any(key in r for key in ("spell", "capital", "typo", "hyphen")):
        return DOT_RED
    if any(
        key in r
        for key in ("grammar", "agreement", "tense", "verb", "article", "punct")
    ):
        return DOT_AMBER
    return DOT_BLUE


def diff_html(
    before: str,
    after: str,
    context: int = 24,
    preserve_newlines: bool = False,
) -> str:
    """Render a pinpoint word/character diff, unchanged text left normal.

    The unchanged context around each change is dimmed and truncated to
    ``context`` characters per side (pass a large context to keep the full
    text, e.g. for the main window's review view). With
    ``preserve_newlines`` the caller must render inside a container that
    honours whitespace (``white-space:pre-wrap``).

    Only the changed words carry colour, weight or a strike-through; the
    surrounding text stays calm so the pinpoint edits are easy to spot.
    """

    def escape(text: str) -> str:
        escaped = html.escape(text)
        return escaped if preserve_newlines else escaped.replace("\n", " ")

    before_tokens = re.findall(r"\S+|\s+", before)
    after_tokens = re.findall(r"\S+|\s+", after)
    matcher = difflib.SequenceMatcher(None, before_tokens, after_tokens)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old = "".join(before_tokens[i1:i2])
        new = "".join(after_tokens[j1:j2])
        if tag == "equal":
            segment = escape(old)
            if len(segment) > context * 2:
                segment = segment[:context] + "…" + segment[-context:]
            if segment:
                parts.append(
                    f"<span style='{CONTEXT_STYLE}'>{segment}</span>"
                )
        elif tag == "delete":
            parts.append(f"<s style='{OLD_STYLE}'>{escape(old)}</s>")
        elif tag == "insert":
            parts.append(f"<span style='{NEW_STYLE}'>{escape(new)}</span>")
        elif tag == "replace":
            parts.append(f"<s style='{OLD_STYLE}'>{escape(old)}</s>")
            parts.append(f"<span style='{ARROW_STYLE}'> → </span>")
            parts.append(f"<span style='{NEW_STYLE}'>{escape(new)}</span>")
    return "".join(parts) or escape(after)
