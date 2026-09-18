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
    arrow: bool = True,
) -> str:
    """Render a pinpoint word/character diff, unchanged text left normal.

    The unchanged context around each change is dimmed and truncated to
    ``context`` characters per side (pass a large context to keep the full
    text, e.g. for the main window's review view). With
    ``preserve_newlines`` the caller must render inside a container that
    honours whitespace (``white-space:pre-wrap``).

    Only the changed words carry colour, weight or a strike-through; the
    surrounding text stays calm so the pinpoint edits are easy to spot. With
    ``arrow`` the replacement is shown as ``old → new``; the review view turns
    it off, because a whole manuscript of arrows reads as noise.
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
            # The struck original and the replacement already read as a pair
            # through their colour and strike-through; a gap is enough, and in
            # the review view (arrow=False) the arrow itself was the noise.
            parts.append(
                f"<span style='{ARROW_STYLE}'> → </span>" if arrow else " "
            )
            parts.append(f"<span style='{NEW_STYLE}'>{escape(new)}</span>")
    return "".join(parts) or escape(after)


# --- App shell -----------------------------------------------------------------
# The live panel above is deliberately cool and blue: it is a suggestion surface
# floating over someone else's document. The window chrome around it - the
# settings dialog, and anything else that is ByteProof's own furniture - is
# ByteMind's warm cream and green, the same values ByteMail renders from its own
# ``ui_theme``. Two surfaces, two moods, one brand.

SHELL_PRIMARY = "#1a3a2a"
SHELL_PRIMARY_FG = "#faf9f3"
SHELL_PRIMARY_600 = "#1f5335"
SHELL_PRIMARY_800 = "#143024"
SHELL_PRIMARY_400 = "#4d8763"
SHELL_PRIMARY_300 = "#79a88a"
SHELL_PRIMARY_200 = "#a9c7b3"
SHELL_PRIMARY_100 = "#d6e4db"
SHELL_PRIMARY_50 = "#edf3ef"

SHELL_BG = "#faf9f3"
SHELL_CARD = "#fdfcf8"
SHELL_MUTED = "#f2efe5"
SHELL_MUTED_STRONG = "#f5f1e8"
SHELL_BORDER = "#e2ddd1"
SHELL_BORDER_LIGHT = "#eae5d9"

SHELL_TEXT = "#1f1e1a"
SHELL_TEXT_SECONDARY = "#3d3b36"
SHELL_TEXT_MUTED = "#6a6760"
SHELL_TEXT_FAINT = "#8a8578"

SHELL_SUCCESS = "#306d49"
SHELL_SUCCESS_BG = "#edf3ef"
SHELL_WARNING = "#9e6b1f"
SHELL_WARNING_BG = "#faf4e6"
SHELL_ERROR = "#a23b32"
SHELL_ERROR_BG = "#fdf2f0"

SHELL_RADIUS_ROW = 8
SHELL_RADIUS_CARD = 12
SHELL_ROW_HEIGHT = 40


def settings_stylesheet(assets_dir: str | None = None) -> str:
    """The settings dialog's whole look, in one string built from the tokens.

    Every rule the dialog needs lives here instead of in per-widget
    ``setStyleSheet`` calls: a colour written in two places drifts, and the
    dialog used to carry its palette in two hundred inline copies of the same
    hex strings. ``assets_dir`` is the folder holding the toggle and chevron
    SVGs; without it the toggle images are omitted and the checkbox falls back
    to the platform indicator.
    """

    arrow = ""
    arrow_up = ""
    if assets_dir:
        folder = assets_dir.replace(chr(92), "/")
        arrow = folder + "/chevron-down.svg"
        arrow_up = folder + "/chevron-up.svg"

    toggle = ""
    if assets_dir:
        folder = assets_dir.replace(chr(92), "/")
        toggle = f"""
QCheckBox::indicator {{ width: 40px; height: 23px; }}
QCheckBox::indicator:unchecked {{ image: url({folder}/toggle-off.svg); }}
QCheckBox::indicator:checked {{ image: url({folder}/toggle-on.svg); }}
"""

    return f"""
QDialog {{ background-color: {SHELL_BG}; }}
QDialog, QDialog * {{ font-size: 12px; color: {SHELL_TEXT}; }}
QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget {{ background: transparent; }}
QLabel {{ color: {SHELL_TEXT}; background: transparent; }}

QLabel#SettingsTitle {{ font-size: 17px; font-weight: 600; }}
QLabel#SettingsSubtitle {{ font-size: 12px; color: {SHELL_TEXT_MUTED}; }}
QLabel#SettingsSectionLabel {{
    font-size: 11px; font-weight: 600; color: {SHELL_TEXT_MUTED};
    padding-top: 6px;
}}
QLabel#SettingsRowTitle {{ font-size: 13px; color: {SHELL_TEXT}; }}
QLabel#SettingsRowHelper {{ font-size: 12px; color: {SHELL_TEXT_MUTED}; }}
QLabel#SettingsHint {{ font-size: 11px; color: {SHELL_TEXT_FAINT}; }}
QLabel#SettingsValue {{ font-size: 12px; color: {SHELL_TEXT_SECONDARY}; }}
QLabel#SettingsCardTitle {{ font-size: 13px; font-weight: 600; color: {SHELL_TEXT}; }}
QLabel#SettingsHero {{ font-size: 15px; font-weight: 600; color: {SHELL_TEXT}; }}
QLabel#SettingsDisplay {{ font-size: 22px; font-weight: 600; color: {SHELL_PRIMARY}; }}
QLabel#SettingsBadge {{
    font-size: 10px; font-weight: 700; padding: 2px 7px;
    border-radius: 6px; background: {SHELL_PRIMARY_50}; color: {SHELL_PRIMARY_600};
}}
QLabel[kind="success"] {{ color: {SHELL_SUCCESS}; }}
QLabel[kind="warning"] {{ color: {SHELL_WARNING}; }}
QLabel[kind="error"] {{ color: {SHELL_ERROR}; }}
QLabel[kind="primary"] {{ color: {SHELL_PRIMARY}; }}
QLabel[kind="muted"] {{ color: {SHELL_TEXT_MUTED}; }}
QLabel[kind="faint"] {{ color: {SHELL_TEXT_FAINT}; }}
QCheckBox {{ color: {SHELL_TEXT}; font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 40px; height: 23px; background: transparent; border: none;
}}
QCheckBox::indicator:hover, QCheckBox::indicator:focus {{
    background: transparent; border: none;
}}

QFrame#SettingsRowDivider {{ background: {SHELL_BORDER_LIGHT}; border: none; max-height: 1px; }}
QFrame#SettingsStatusBar {{
    background: {SHELL_CARD}; border: none;
    border-top: 1px solid {SHELL_BORDER_LIGHT};
}}
QLabel#SettingsStatusGlyph {{ font-size: 12px; font-weight: 700; color: {SHELL_TEXT_FAINT}; }}
QLabel#SettingsStatusText {{ font-size: 12px; color: {SHELL_TEXT_MUTED}; }}
QLabel#SettingsStatusText[kind="success"] {{ color: {SHELL_SUCCESS}; }}
QLabel#SettingsStatusText[kind="error"] {{ color: {SHELL_ERROR}; }}
QLabel#SettingsStatusGlyph[kind="success"] {{ color: {SHELL_SUCCESS}; }}
QLabel#SettingsStatusGlyph[kind="error"] {{ color: {SHELL_ERROR}; }}
QLabel#SettingsStatusGlyph[kind="info"] {{ color: {SHELL_TEXT_FAINT}; }}

QFrame#ProviderCard:hover {{ border-color: {SHELL_BORDER}; }}
QListWidget#SettingsAppList {{
    background: {SHELL_CARD}; border: 1px solid {SHELL_BORDER_LIGHT};
    border-radius: {SHELL_RADIUS_CARD}px; padding: 4px;
}}
QListWidget#SettingsAppList::item {{
    border: none; min-height: {SHELL_ROW_HEIGHT}px;
    border-radius: {SHELL_RADIUS_ROW}px;
}}
QListWidget#SettingsAppList::item:hover {{ background: {SHELL_MUTED}; }}
#LiveAppRow {{ background: transparent; border-radius: 6px; }}
#LiveAppRow:hover {{ background: {SHELL_MUTED}; }}
QToolButton#LiveAppRemove {{
    border: none; color: {SHELL_TEXT_FAINT}; font-size: 14px; font-weight: 700;
    padding: 2px 6px; border-radius: 6px;
}}
QToolButton#LiveAppRemove:hover {{ color: {SHELL_ERROR}; background: {SHELL_ERROR_BG}; }}
QLabel#SettingsBadge {{
    background: {SHELL_SUCCESS_BG}; color: {SHELL_SUCCESS}; font-size: 9px;
    font-weight: 700; padding: 2px 6px; border-radius: 4px;
}}
QFrame#ProviderCard, QFrame#LicenseCard, QFrame#SettingsCard {{
    background: {SHELL_CARD};
    border: 1px solid {SHELL_BORDER_LIGHT};
    border-radius: {SHELL_RADIUS_CARD}px;
}}
QFrame#SettingsCallout {{
    background: {SHELL_PRIMARY_50};
    border: 1px solid {SHELL_PRIMARY_200};
    border-radius: {SHELL_RADIUS_CARD}px;
}}
#AutomationRuleCard {{
    background: {SHELL_CARD};
    border: 1px solid {SHELL_BORDER_LIGHT};
    border-radius: {SHELL_RADIUS_CARD}px;
}}
#AutomationRuleCard:hover {{ border-color: {SHELL_BORDER}; }}
#AutomationRuleCard[selected="true"] {{
    background: {SHELL_PRIMARY_50}; border-color: {SHELL_PRIMARY_200};
}}
QGroupBox {{
    background: {SHELL_CARD};
    border: 1px solid {SHELL_BORDER_LIGHT};
    border-radius: {SHELL_RADIUS_CARD}px;
    margin-top: 14px; padding: 16px;
    font-size: 12px; font-weight: 600; color: {SHELL_TEXT_MUTED};
}}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 14px; padding: 0 6px; }}

QPushButton {{
    background: {SHELL_CARD}; color: {SHELL_TEXT};
    border: 1px solid {SHELL_BORDER}; border-radius: {SHELL_RADIUS_ROW}px;
    padding: 7px 14px; min-height: 16px; min-width: 68px;
    font-size: 12px; font-weight: 500;
}}
QPushButton#SmallBtn {{ padding: 4px 11px; min-height: 14px; min-width: 64px; }}
QPushButton#LinkBtn {{
    background: transparent; border: none; color: {SHELL_PRIMARY};
    padding: 2px 0; min-height: 0; min-width: 0; font-weight: 500;
}}
QPushButton#LinkBtn:hover {{ color: {SHELL_PRIMARY_600}; text-decoration: underline; }}
QPushButton:hover {{ background: {SHELL_PRIMARY_50}; border-color: {SHELL_PRIMARY_200}; }}
QPushButton:pressed {{ background: {SHELL_PRIMARY_100}; }}
QPushButton:focus {{ outline: none; }}
QPushButton#PrimaryBtn:pressed {{ background: {SHELL_PRIMARY_800}; }}
QPushButton#PrimaryBtn:disabled {{
    background: {SHELL_PRIMARY_300}; border-color: {SHELL_PRIMARY_300};
    color: {SHELL_PRIMARY_FG};
}}
QPushButton:disabled {{ color: {SHELL_TEXT_FAINT}; background: {SHELL_MUTED}; border-color: {SHELL_BORDER_LIGHT}; }}
QPushButton:default, QPushButton#PrimaryBtn {{
    background: {SHELL_PRIMARY}; color: {SHELL_PRIMARY_FG}; border: 1px solid {SHELL_PRIMARY_800};
}}
QPushButton:default:hover, QPushButton#PrimaryBtn:hover {{
    background: {SHELL_PRIMARY_600}; border-color: {SHELL_PRIMARY_800};
}}
QPushButton#DangerBtn {{ color: {SHELL_ERROR}; }}
QPushButton#DangerBtn:hover {{ background: {SHELL_ERROR_BG}; border-color: {SHELL_ERROR}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QKeySequenceEdit, QPlainTextEdit, QTextEdit {{
    background: {SHELL_CARD}; color: {SHELL_TEXT};
    border: 1px solid {SHELL_BORDER}; border-radius: {SHELL_RADIUS_ROW}px;
    padding: 6px 10px; min-height: 17px; font-size: 12px;
    selection-background-color: {SHELL_PRIMARY_100};
}}
QComboBox {{ min-width: 190px; padding-right: 26px; }}
QComboBox:hover, QComboBox:focus, QComboBox:on {{ border-color: {SHELL_PRIMARY_400}; }}
QComboBox:disabled {{ color: {SHELL_TEXT_FAINT}; background: {SHELL_MUTED}; }}
QComboBox::drop-down {{ background: transparent; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QKeySequenceEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {SHELL_PRIMARY_400};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QSpinBox, QDoubleSpinBox {{ min-width: 88px; padding-right: 4px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 20px; height: 12px; margin: 2px 3px 0 0;
    border: none; background: transparent; border-radius: 4px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 20px; height: 12px; margin: 0 3px 2px 0;
    border: none; background: transparent; border-radius: 4px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {SHELL_PRIMARY_50};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({arrow_up}); width: 9px; height: 5px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({arrow}); width: 9px; height: 5px;
}}
QComboBox::down-arrow {{ image: url({arrow}); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{ font-size: 12px; padding: 6px; }}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 4px 8px; }}
QComboBox QAbstractItemView::item:hover {{ background: {SHELL_PRIMARY_50}; }}
QComboBox QAbstractItemView::item:selected {{
    background: {SHELL_PRIMARY_50}; color: {SHELL_TEXT};
}}
QComboBox QAbstractItemView {{
    background: {SHELL_CARD}; border: 1px solid {SHELL_BORDER};
    selection-background-color: {SHELL_PRIMARY_50}; selection-color: {SHELL_TEXT};
    outline: none; padding: 4px;
}}

QSlider::groove:horizontal {{ height: 4px; background: {SHELL_BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {SHELL_PRIMARY_300}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {SHELL_PRIMARY}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QProgressBar {{
    background: {SHELL_MUTED}; border: none; border-radius: 9px;
    min-height: 18px; text-align: center; color: {SHELL_TEXT_MUTED};
    font-size: 11px;
}}
QProgressBar::chunk {{ background: {SHELL_PRIMARY}; border-radius: 8px; }}

QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ color: {SHELL_TEXT_SECONDARY}; }}
QListWidget::item:selected {{ background: {SHELL_PRIMARY_50}; color: {SHELL_TEXT}; }}
QListWidget#SettingsSidebar {{ padding: 2px 0 10px 0; font-size: 13px; }}
QListWidget#SettingsSidebar::item {{
    height: {SHELL_ROW_HEIGHT}px; margin: 1px 10px; padding-left: 10px; padding-right: 10px;
    border-radius: {SHELL_RADIUS_ROW}px; color: {SHELL_TEXT}; font-weight: 500;
}}
QListWidget#SettingsSidebar::item:hover:!selected {{ background: {SHELL_MUTED}; }}
QListWidget#SettingsSidebar::item:selected {{
    background: {SHELL_PRIMARY}; color: {SHELL_PRIMARY_FG}; font-weight: 600;
}}

QLabel#SettingsIdentityTitle {{ font-size: 13px; font-weight: 600; }}
QLabel#SettingsIdentityMeta {{ font-size: 11px; color: {SHELL_TEXT_MUTED}; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {SHELL_BORDER}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {SHELL_PRIMARY_200}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {SHELL_BORDER}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolButton {{ border: none; background: transparent; color: {SHELL_TEXT_SECONDARY}; }}
QToolTip {{ background: {SHELL_TEXT}; color: {SHELL_PRIMARY_FG}; border: none; padding: 4px 6px; }}
{toggle}
"""

