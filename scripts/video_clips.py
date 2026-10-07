"""The ByteProof demonstration films: what each one shows, and what is said.

One entry per film, always the same shape: a title card, four or five steps, a
closing card. A viewer who stops halfway should still know where they are.

``say`` holds the narration and has one line for the title card and one for
each step. The closing line is not written twice: it is taken from the closing
card's own strap, so the two cannot drift apart.

Two things the writing here is careful about:

* **It has to sound like a person.** No em dashes, no stacked three-part lists,
  no stock phrases. Short sentences next to longer ones, and contractions where
  a person would use them. ``check_clips()`` refuses an em dash.
* **It has to describe the screen it plays over.** A step that produces a
  result speaks after the result appears, not before it. The audit sheet is the
  only cheap way to check that, and it is worth reading before a full run.

The screens are the application's own widgets, driven through the same methods
its event handlers call. Nothing here invents a screen that the product does
not have, and nothing waits on a network.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

import record_website_clips as rwc
from record_demo_videos import (
    INK,
    PRIMARY,
    Beat,
    Clip,
    draw_paragraph,
    film,
    qfont,
    window_frame,
)

BRAND = {
    "name": "ByteProof",
    "subtitle": "Proofreading",
    "site": "bytemind.co.nz",
    "foot": "ByteProof · bytemind.co.nz",
}

GROUPS = {
    "01-getting-started": "Getting started",
    "02-microsoft-word": "Microsoft Word",
    "03-live-check": "Live Check",
    "04-anywhere-else": "Anywhere else",
    "05-where-the-ai-runs": "Where the proofreading runs",
    "06-licence": "Licence and updates",
}


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

# One paragraph, used almost everywhere a document is on screen. It has four
# of the things ByteProof exists to catch and two citations, so the same page
# can be filmed for the plain proofread, the citation protection and the
# comment modes without the viewer learning three different documents.
DOC_WORDS = ["We", "utilise", "a", "range", "of", "methods", "in", "order", "to", "examine", "the", "relationship", "between", "governance", "and", "firm", "performance", "due", "to", "the", "fact", "that", "the", "data", "is", "limited."]

CHANGES = (
    ("utilise", "use", "Plainer word"),
    ("in order to", "to", "Wordy phrase"),
    ("due to the fact that", "because", "Wordy phrase"),
)

# The paragraphs either side of the one being proofread. They exist so the page
# reads as a page: a paragraph floating on its own looks like a test fixture,
# and a viewer has to be able to see that this is one passage of a longer
# document rather than the whole of it.
BEFORE_TEXT = (
    "This chapter examines how board structure and ownership shape the quality "
    "of financial reporting in listed firms. The evidence comes from a panel of "
    "214 firms over the period from 2011 to 2021."
)
AFTER_TEXT = (
    "The chapter is organised as follows. The next section sets out the "
    "measurement of the variables, and the section after that describes the "
    "estimation strategy."
)

DELETION = "#C00000"
INSERTION = "#107C41"


class MarkedSheet(rwc.Sheet):
    """The page after Apply, drawn the way Word records a revision.

    Word keeps the words it took out, struck through, and puts the replacement
    beside them in the colour it uses for an insertion. The page is otherwise
    unchanged, so this is the same page with that markup on it rather than a
    second design for a document.
    """

    def __init__(self, rect, words: Sequence[str], **kwargs) -> None:
        super().__init__(rect, words, **kwargs)
        self.marks: dict[int, str] = {}

    def revise(self, before: str, after: str) -> list[int]:
        first, last = self.index_of(before)
        removed = [token.text for token in self.tokens[first:last + 1]]
        self.tokens[first:last + 1] = [rwc.Token(word) for word in removed] + [
            rwc.Token(word) for word in after.split(" ")
        ]
        for offset in range(len(removed)):
            self.marks[first + offset] = "delete"
        for offset in range(len(removed), len(removed) + len(after.split(" "))):
            self.marks[first + offset] = "insert"
        return list(range(first, first + len(removed) + len(after.split(" "))))

    def draw(self, painter, selection=None, fixes=None) -> None:
        from PyQt6.QtCore import QPointF, QRectF, Qt
        from PyQt6.QtGui import QColor, QPen

        super().draw(painter, selection=selection, fixes=fixes)
        if not self.marks:
            return
        painter.save()
        painter.setFont(self.font())
        for index, kind in self.marks.items():
            rect = self.token_rect(index)
            # The page behind is one flat colour, so the plain word the parent
            # drew is painted over rather than covered by two shades of text.
            # A tint from `fixes` is kept, because that is the flash that says
            # the change has just landed.
            tint = (fixes or {}).get(index)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tint if tint is not None else QColor(rwc.SHEET))
            painter.drawRect(rect.adjusted(-4.0, 0.0, 4.0, 0.0))
            colour = DELETION if kind == "delete" else INSERTION
            painter.setPen(QColor(colour))
            painter.drawText(
                QRectF(rect.left(), rect.top(), rect.width() + 3, rect.height()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                self.tokens[index].text,
            )
            y = rect.center().y() + (1.0 if kind == "delete" else 9.0)
            painter.setPen(QPen(QColor(colour), 1.5))
            painter.drawLine(
                QPointF(rect.left() - 2.0, y), QPointF(rect.right() + 2.0, y)
            )
        painter.restore()


class WordStage:
    """A document in Word, and the card that floats over it.

    The document itself is drawn: Microsoft Word is not part of this
    application and a film of it would be a film of somebody else's software.
    What is real is everything ByteProof puts on top - the floating card is the
    shipped ``WordSuggestionCard``, filled by ``set_spans``, and the buttons
    are found in it the way a click would find them.
    """

    WINDOW = (110.0, 76.0, 768.0, 590.0)
    PAGE = (172.0, 138.0, 644.0, 512.0)
    CARD_AT = (948.0, 214.0)
    SHEET = (178.0, 268.0, 632.0, 200.0)
    COLUMN = 214.0
    COLUMN_WIDTH = 560.0

    def __init__(self, words: Sequence[str] | None = None,
                 title: str = "Microsoft Word") -> None:
        self.title = title
        self.words = list(words or DOC_WORDS)
        self.sheet = MarkedSheet(self.SHEET, list(self.words))
        self.card = None
        self.card_image = None

    # -- state ---------------------------------------------------------
    def spans(self, changes=CHANGES):
        from src.live_preview import EditSpan

        return [
            EditSpan(before=before, after=after, reason=reason,
                     start=0, end=len(before))
            for before, after, reason in changes
        ]

    def make_card(self, changes=CHANGES):
        self.card, self.card_image = build_card(changes)
        return self.card_image

    def label_rect(self, text: str):
        """Canvas rectangle of a labelled part of the card, for a ring."""
        from PyQt6.QtCore import QPoint, QRectF
        from PyQt6.QtWidgets import QLabel

        if self.card is None:
            return None
        for label in self.card.findChildren(QLabel):
            if label.text() != text:
                continue
            at = label.mapTo(self.card, QPoint(0, 0))
            return QRectF(
                self.CARD_AT[0] + at.x() - 6.0, self.CARD_AT[1] + at.y() - 4.0,
                label.width() + 12.0, label.height() + 8.0,
            )
        return None

    def apply(self, changes=CHANGES) -> list[int]:
        """Run the changes through the document, the way Apply all does."""
        touched: list[int] = []
        for before, after, _reason in changes:
            touched.extend(self.sheet.revise(before, after))
        return touched

    def changed_tokens(self, changes=CHANGES) -> list[int]:
        found: list[int] = []
        for before, after, _reason in changes:
            try:
                first, _last = self.sheet.index_of(before)
            except ValueError:
                continue
            found.extend(range(first, first + len(after.split(" "))))
        return found

    def button(self, label: str, index: int = 0):
        """Canvas coordinates of a button on the card."""
        if self.card is None:
            return None
        point = rwc.widget_center(self.card, label, index)
        if point is None:
            return None
        return self.CARD_AT[0] + point[0], self.CARD_AT[1] + point[1]

    def card_box(self, image=None):
        from PyQt6.QtCore import QRectF

        image = image or self.card_image
        if image is None:
            return None
        return QRectF(self.CARD_AT[0], self.CARD_AT[1], image.width(), image.height())

    # -- drawing -------------------------------------------------------
    def draw_page(self, painter, selection=None, fixes=None, hints=None) -> None:
        from PyQt6.QtCore import QRectF, Qt
        from PyQt6.QtGui import QColor, QPen

        content = window_frame(painter, QRectF(*self.WINDOW), self.title)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#eeece5"))
        painter.drawRect(content)
        painter.restore()
        rwc.draw_shadow(painter, QRectF(*self.PAGE), 3.0, dy=8, blur=20, alpha=30)
        painter.save()
        painter.setPen(QPen(QColor("#e7e3da"), 1.0))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRect(QRectF(*self.PAGE))
        painter.restore()

        body = qfont(("Helvetica Neue", "Inter", "Arial"), 13.5)
        draw_paragraph(painter, self.COLUMN, 204.0, BEFORE_TEXT, body,
                       "#a8a29b", self.COLUMN_WIDTH, leading=25.0)
        draw_paragraph(painter, self.COLUMN, 500.0, AFTER_TEXT, body,
                       "#a8a29b", self.COLUMN_WIDTH, leading=25.0)
        if hints:
            # A field the application protects rather than edits: a citation,
            # drawn the way Word marks one, in the accent the app uses for it.
            for label, tone in hints:
                try:
                    first, last = self.sheet.index_of(label)
                except ValueError:
                    continue
                start = self.sheet.token_rect(first)
                end = self.sheet.token_rect(last)
                box = QRectF(start.left() - 6.0, start.top() + 3.0,
                             end.right() - start.left() + 12.0,
                             start.height() - 6.0)
                painter.save()
                painter.setPen(0)
                colour = QColor(tone)
                colour.setAlpha(170)
                painter.setBrush(colour)
                painter.drawRoundedRect(box, 5.0, 5.0)
                painter.restore()
        self.sheet.draw(painter, selection=selection, fixes=fixes)

    def draw_card(self, painter, alpha: float = 1.0, dy: float = 0.0,
                  image=None) -> None:
        from PyQt6.QtCore import QRectF

        image = image or self.card_image
        if image is None or alpha <= 0.01:
            return
        box = QRectF(self.CARD_AT[0], self.CARD_AT[1] + dy,
                     image.width(), image.height())
        painter.save()
        painter.setOpacity(alpha)
        rwc.draw_shadow(painter, box, 14.0, dy=14, blur=30, alpha=44)
        painter.drawImage(int(box.left()), int(box.top()), image)
        painter.restore()


def note(heading: str, body: str) -> dict:
    return {"heading": heading, "body": body}


def build_card(changes=CHANGES):
    """A suggestion card in the state these changes would put it in."""
    from src.live_preview import EditSpan

    spans = [
        EditSpan(before=before, after=after, reason=reason,
                 start=0, end=len(before))
        for before, after, reason in changes
    ]
    return rwc.build_card(spans)


def demo_settings() -> dict:
    """A copy of the preferences with anything personal taken out.

    These films are published, and the settings dialog is filmed from the
    machine the recorder runs on. API keys, the apps a particular person has
    added and anything else that was configured here would otherwise appear in
    the picture. What is left is the shape a new installation has.
    """
    from src import settings as settings_mod

    data = settings_mod.load_runtime_settings()
    providers = data.get("providers")
    if isinstance(providers, dict):
        for provider in providers.values():
            if isinstance(provider, dict):
                provider["api_keys"] = []
    live = data.get("live_preview")
    if isinstance(live, dict):
        live["hidden_apps"] = []
        live["app_rules"] = {}
    data["active_provider"] = "ByteProof Local"
    return data


class SettingsStage:
    """The real settings dialog, on the canvas, with its controls pointable.

    The dialog is the shipped one, so a film can open a page, move a slider or
    pick from a list and the picture is whatever the application does with
    that. Nothing here reaches the user's settings file: the dialog is built
    from a copy of the defaults and never saved.
    """

    ROWS: ClassVar[dict[str, int]] = {
        "general": 0,
        "live": 1,
        "automation": 2,
        "connect": 3,
        "local": 4,
        "license": 5,
        "updates": 6,
    }

    def __init__(self, row: str = "general", size=(900, 620),
                 at=(350.0, 96.0), title="ByteProof Settings") -> None:
        from src.gui import SettingsDialog

        self.at = at
        self.title = title
        self.dialog = SettingsDialog(demo_settings())
        rwc.hide_widget(self.dialog)
        self.dialog.resize(*size)
        self.dialog.sidebar.setCurrentRow(self.ROWS[row])
        self.dialog.change_page(self.ROWS[row])
        rwc.flush()
        self.image = rwc.snap(self.dialog, 14.0)

    def refresh(self) -> None:
        rwc.flush()
        self.image = rwc.snap(self.dialog, 14.0)

    def show(self, widget, margin: float = 40.0) -> None:
        """Scroll the page so a control is actually on screen when it is filmed.

        The pages are taller than the dialog, so the row a film is about can
        sit below the fold. A ring around something half cut off by the bottom
        edge is worse than no ring at all.
        """
        from PyQt6.QtWidgets import QScrollArea

        for area in self.dialog.findChildren(QScrollArea):
            if area.isAncestorOf(widget):
                area.ensureWidgetVisible(widget, 0, int(margin))
                break
        self.refresh()

    def point(self, widget) -> tuple[float, float]:
        from PyQt6.QtCore import QPoint

        centre = widget.mapTo(
            self.dialog, QPoint(widget.width() // 2, widget.height() // 2)
        )
        return self.at[0] + centre.x(), self.at[1] + 40.0 + centre.y()

    def row_rect(self, widget, pad: float = 12.0):
        from PyQt6.QtCore import QPoint, QRectF

        top_left = widget.mapTo(self.dialog, QPoint(0, 0))
        return QRectF(
            self.at[0] + top_left.x() - pad,
            self.at[1] + 40.0 + top_left.y() - pad,
            widget.width() + pad * 2,
            widget.height() + pad * 2,
        )

    def draw(self, painter, alpha: float = 1.0, dy: float = 0.0) -> None:
        from PyQt6.QtCore import QRectF

        box = QRectF(self.at[0], self.at[1] + dy, self.image.width(),
                     self.image.height() + 40.0)
        window_frame(painter, box, self.title)
        painter.save()
        painter.setOpacity(alpha)
        painter.drawImage(int(box.left()) + 1, int(box.top()) + 40, self.image)
        painter.restore()


class MainStage:
    """ByteProof's own window, holding a finished proofread."""

    def __init__(self, at=(120.0, 84.0), size=(900, 620), html: str | None = None,
                 status: str = "Ready", live: str = "Live Check: watching for "
                 "selections") -> None:
        from src import gui as gui_mod

        self.at = at
        self.window = gui_mod.ProofreaderApp(2000, demo_settings())
        rwc.hide_widget(self.window)
        self.window.resize(*size)
        if self.window.menuBar() is not None:
            self.window.menuBar().hide()
        if html:
            self.window.diff_text.setHtml(html)
        self.window.copy_btn.setEnabled(bool(html))
        self.window.status_label.setText(status)
        self.window.live_status_label.setText(live)
        rwc.flush()
        self.image = rwc.snap(self.window, 16.0)

    def refresh(self) -> None:
        rwc.flush()
        self.image = rwc.snap(self.window, 16.0)

    def point(self, widget) -> tuple[float, float]:
        from PyQt6.QtCore import QPoint

        centre = widget.mapTo(
            self.window, QPoint(widget.width() // 2, widget.height() // 2)
        )
        return self.at[0] + centre.x(), self.at[1] + 40.0 + centre.y()

    def draw(self, painter, alpha: float = 1.0, dy: float = 0.0) -> None:
        from PyQt6.QtCore import QRectF

        box = QRectF(self.at[0], self.at[1] + dy, self.image.width(),
                     self.image.height() + 40.0)
        window_frame(painter, box, "ByteProof")
        painter.save()
        painter.setOpacity(alpha)
        painter.drawImage(int(box.left()) + 1, int(box.top()) + 40, self.image)
        painter.restore()


# ---------------------------------------------------------------------------
# 01. What ByteProof does
# ---------------------------------------------------------------------------


def build_what_it_does() -> Clip:
    main = MainStage(at=(120.0, 84.0), size=(900, 620), html=diff_html())
    stage = WordStage()
    stage.make_card()
    other = MailStage()
    settings = SettingsStage("connect", size=(900, 620), at=(350.0, 96.0))

    def first(painter, t, beat):
        main.draw(painter)

    def second(painter, t, beat):
        appear = rwc.between(t, 0.2, 1.0)
        stage.draw_page(painter, selection=(0, len(DOC_WORDS) - 1,
                                            rwc.smooth(rwc.between(t, 0.4, 1.4))))
        stage.draw_card(painter, rwc.ease_out(appear), (1 - rwc.ease_out(appear)) * 22)

    def third(painter, t, beat):
        other.draw(painter, t)

    def fourth(painter, t, beat):
        settings.draw(painter)

    return Clip(
        name="01-what-byteproof-does",
        group="01-getting-started",
        card={
            "kicker": "ByteProof",
            "title": "What ByteProof does",
            "strap": "It reads what you select, and offers changes you can read "
                     "before any of them are made.",
            "foot": BRAND["foot"],
            "series": "Getting started",
        },
        end={
            "kicker": "What this shows",
            "title": "One shortcut, two kinds of answer",
            "strap": "In Word you get tracked changes. Everywhere else, the "
                     "selection is replaced with the finished sentence.",
        },
        say="ByteProof sits alongside the applications you already write in. You "
            "select a passage, press a shortcut, and it answers with changes you "
            "can read before any of them are made.",
        beats=[
            Beat(
                caption="The application",
                detail="A status line, the changes it last proposed, and the "
                       "button that starts another proofread.",
                say="This is the whole application: what it is doing right now, "
                    "the changes it last proposed, and one button to start "
                    "another proofread.",
                draw=first,
                note=note("Nothing to add to Word",
                          "ByteProof reads other windows. It does not install an "
                          "add-in, so there is nothing new inside Word to trust."),
                cursor=[(0.05, 1180.0, 760.0), (0.45, 980.0, 690.0)],
            ),
            Beat(
                caption="In Word",
                detail="A selected paragraph comes back as tracked changes you "
                       "accept or reject.",
                say="In Word, a selection comes back as tracked changes, so a "
                    "supervisor can see exactly what moved.",
                draw=second,
                note=note("Word fields are read, not rewritten",
                          "Citations and equations are located first and left "
                          "alone, so a reference cannot be quietly reworded."),
                cursor=[(0.08, 980.0, 740.0), (0.5, 900.0, 420.0)],
            ),
            Beat(
                caption="Anywhere else",
                detail="The same shortcut works in a mail window, a browser, or "
                       "any editor with selectable text.",
                say="Anywhere else, the same shortcut polishes the selection and "
                    "puts the finished sentence back where it was.",
                draw=third,
                note=note("The selection is replaced",
                          "There is nothing to copy and paste: ByteProof writes "
                          "the polished sentence back over the original."),
                cursor=[(0.05, 1080.0, 700.0), (0.42, 660.0, 400.0)],
            ),
            Beat(
                caption="Where it runs",
                detail="A local model on this machine, or an API key you already "
                       "have. Nothing is bundled and nothing is hidden.",
                say="Proofreading can run on a small model on this machine, or on "
                    "a key you already have. Which one is a decision you make "
                    "once, in settings.",
                draw=fourth,
                note=note("Offline is a real option",
                          "The local model downloads once, then proofreads with "
                          "the network off."),
                cursor=[(0.06, 1240.0, 700.0), (0.5, 1120.0, 430.0)],
            ),
        ],
    )


def diff_html() -> str:
    """The Proposed Changes pane, in the shape the app writes it."""
    return (
        "<p style='font-family:Menlo;font-size:12px;color:#44403C'>"
        "We <span style='color:#B91C1C;text-decoration:line-through'>utilise</span>"
        "<span style='color:#15803D'> use</span> a range of methods "
        "<span style='color:#B91C1C;text-decoration:line-through'>in order to</span>"
        "<span style='color:#15803D'> to</span> examine the relationship between "
        "governance and firm performance "
        "<span style='color:#B91C1C;text-decoration:line-through'>due to the fact "
        "that</span><span style='color:#15803D'> because</span> the data is limited.</p>"
    )


MAIL_WORDS = ["We", "utilise", "a", "number", "of", "approaches", "in", "order", "to", "establish", "the", "relationship", "between", "the", "two", "measures", "due", "to", "the", "fact", "that", "the", "sample", "is", "small."]

MAIL_CHANGES = (
    ("utilise", "use", "Plainer word"),
    ("in order to", "to", "Wordy phrase"),
    ("due to the fact that", "because", "Wordy phrase"),
)


class MailStage:
    """Somebody else's window, with the live overlay on top of it.

    The underlines are the shipped ``LiveOverlay``, given the same rectangles
    the application would give it for those words. The panel that opens from a
    click is the shipped ``_SuggestionPopup``, filled by ``show_popup``.
    """

    WINDOW = (150.0, 116.0, 760.0, 534.0)
    BODY = (160.0, 324.0, 740.0, 176.0)

    def __init__(self, words: Sequence[str] | None = None,
                 changes=MAIL_CHANGES, title: str = "New Message") -> None:
        from src.live_overlay import LiveOverlay, UndoPill

        self.title = title
        self.changes = changes
        self.words = list(words or MAIL_WORDS)
        self.sheet = rwc.Sheet(self.BODY, list(self.words))
        self.overlay = LiveOverlay()
        rwc.hide_widget(self.overlay)
        self.overlay_image = None
        self.popup_image = None
        self.popup_at = None
        self.popup_index = None
        self.pill = UndoPill()
        rwc.hide_widget(self.pill)
        self.pill_image = rwc.snap(self.pill, 16.0)

    # -- state ---------------------------------------------------------
    def spans(self):
        from PyQt6.QtCore import QRect

        from src.live_overlay import OverlaySpan

        self.sheet.layout()
        found = []
        for before, after, reason in self.changes:
            try:
                first, last = self.sheet.index_of(before)
            except ValueError:
                continue
            start = self.sheet.token_rect(first)
            end = self.sheet.token_rect(last)
            found.append(
                OverlaySpan(
                    before=before, after=after, reason=reason,
                    rect=QRect(int(start.left()) - 2, int(start.top()),
                               int(end.right() - start.left()) + 4,
                               int(start.height()) - 6),
                )
            )
        return found

    def paint_overlay(self):
        """Re-render the underline layer for the words still to change."""
        spans = self.spans()
        self.overlay.set_spans(spans)
        # The shipped overlay masks itself to the underlines so it can be
        # click-through. A recording grabs the whole canvas instead, so the
        # dashed lines land exactly where the text is.
        self.overlay.clearMask()
        self.overlay.setGeometry(0, 0, 1600, 900)
        rwc.flush()
        self.overlay_image = rwc.snap(self.overlay)
        return spans

    def open_popup(self, index: int):
        from PyQt6.QtGui import QColor

        spans = self.spans()
        if index >= len(spans):
            return None
        self.overlay.show_popup(index)
        rwc.flush()
        popup = self.overlay._popup
        if popup is None:
            return None
        # The panel is transparent on screen, so the recorder gives it the
        # surface colour the design asks for. Without it the card is unreadable
        # over the text.
        self.popup_image = rwc.snap(popup, 14.0, background=QColor("#FFFFFF"))
        self.popup_index = index
        span = spans[index]
        # Under the paragraph rather than over the following lines: the panel
        # is tall, and covering the text it is talking about helps nobody.
        self.popup_at = (
            float(span.rect.left()),
            float(self.sheet.bounds().bottom() + 20.0),
        )
        return self.popup_at

    def close_popup(self) -> None:
        self.overlay.hide_popup()
        self.popup_image = None
        self.popup_index = None

    def apply(self, index: int) -> tuple[int, int]:
        before, after, _reason = self.changes[index]
        first, last = self.sheet.index_of(before)
        self.sheet.replace(first, last, after)
        self.changes = tuple(
            change for position, change in enumerate(self.changes) if position != index
        )
        self.close_popup()
        self.paint_overlay()
        return first, first + len(after.split(" "))

    def caret(self, index: int = 0):
        """A point just after a change, for the pointer to rest on."""
        spans = self.spans()
        if not spans:
            return (900.0, 420.0)
        rect = spans[min(index, len(spans) - 1)].rect
        return float(rect.right() + 16), float(rect.center().y())

    # -- drawing -------------------------------------------------------
    def draw(self, painter, alpha: float = 1.0, overlay: bool = True,
             selection=None, fixes=None) -> None:
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor

        painter.save()
        painter.setOpacity(alpha)
        content = window_frame(painter, QRectF(*self.WINDOW), self.title)
        painter.setPen(QColor("#8a8579"))
        painter.setFont(qfont(("Helvetica Neue", "Inter", "Arial"), 13.5))
        painter.drawText(int(content.left() + 46), int(content.top() + 44),
                         "To:  Priya Raman")
        painter.drawText(int(content.left() + 46), int(content.top() + 74),
                         "Subject:  Draft methods section")
        painter.setPen(QColor("#ded8ca"))
        painter.drawRect(
            QRectF(content.left() + 46, content.top() + 96, content.width() - 92, 1)
        )
        painter.setPen(QColor(INK))
        painter.setFont(qfont(("Helvetica Neue", "Inter", "Arial"), 15.0))
        painter.drawText(int(content.left() + 46), int(content.top() + 140), "Hi Priya,")
        for row, line in enumerate(("Thanks,", "Tom")):
            painter.drawText(
                int(content.left() + 46), int(content.top() + 400 + row * 28), line
            )
        painter.restore()
        self.sheet.draw(painter, selection=selection, fixes=fixes)
        if overlay and self.overlay_image is not None:
            painter.save()
            painter.setOpacity(alpha)
            painter.drawImage(0, 0, self.overlay_image)
            painter.restore()

    def draw_popup(self, painter, alpha: float = 1.0) -> None:
        from PyQt6.QtCore import QRectF

        if self.popup_image is None or self.popup_at is None:
            return
        box = QRectF(self.popup_at[0], self.popup_at[1],
                     self.popup_image.width(), self.popup_image.height())
        painter.save()
        painter.setOpacity(alpha)
        rwc.draw_shadow(painter, box, 14.0, dy=12, blur=26, alpha=40)
        painter.drawImage(int(box.left()), int(box.top()), self.popup_image)
        painter.restore()


# ---------------------------------------------------------------------------
# 02. Proofreading a selection
# ---------------------------------------------------------------------------


def build_proofread_a_selection() -> Clip:
    from src.gui import ToastNotification

    stage = WordStage()
    card_image = stage.make_card()
    apply_at = stage.button("Apply all") or (1120.0, 500.0)

    toast = ToastNotification()
    rwc.hide_widget(toast)
    toast.complete("Applied 3 changes to Word", "success", duration_ms=600000)
    toast.hide()
    toast_image = rwc.snap(toast, 18.0)

    state = {"applied": False, "touched": []}

    def apply_all() -> None:
        state["touched"] = stage.apply()
        state["applied"] = True
        stage.card_image = None

    def draw_selection(painter, t, beat):
        last = len(DOC_WORDS) - 1
        stage.draw_page(
            painter,
            selection=(0, last, rwc.smooth(rwc.between(t, 0.25, 1.5))),
        )

    def draw_shortcut(painter, t, beat):

        stage.draw_page(painter, selection=(0, len(DOC_WORDS) - 1, 1.0))
        bounds = stage.sheet.bounds()
        scale = 0.9 + 0.1 * rwc.ease_out(rwc.between(t, 0.1, 0.7))
        painter.save()
        painter.setOpacity(rwc.between(t, 0.05, 0.5))
        # In the gap under the paragraph, not over the paragraph after it.
        rwc.draw_key_chip(painter, bounds.left() + 240.0, bounds.bottom() + 34.0,
                          ["⌥", "⌘", "P"], scale)
        painter.restore()

    def draw_card(painter, t, beat):
        stage.draw_page(painter, selection=(0, len(DOC_WORDS) - 1, 1.0))
        if state["applied"]:
            return
        amount = rwc.ease_out(rwc.between(t, 0.25, 0.9))
        stage.draw_card(painter, amount, (1.0 - amount) * 26.0, image=card_image)

    def draw_applied(painter, t, beat):
        flash = {index: rwc.flash_colour(t, 0.3) for index in state["touched"]}
        stage.draw_page(painter, fixes=flash)
        amount = rwc.ease_out(rwc.between(t, 0.5, 1.0))
        if amount > 0.02:
            painter.save()
            painter.setOpacity(amount)
            painter.drawImage(int((1600 - toast_image.width()) / 2), 682, toast_image)
            painter.restore()

    return Clip(
        name="02-proofread-a-selection",
        group="02-microsoft-word",
        card={
            "kicker": "Microsoft Word",
            "title": "Proofreading a selection",
            "strap": "Choose the passage, press the shortcut, and read what "
                     "ByteProof would change.",
            "foot": BRAND["foot"],
            "series": "Microsoft Word",
        },
        end={
            "kicker": "What this shows",
            "title": "Nothing is written until you say so",
            "strap": "Every change waits in the card, and you decide what lands "
                     "in the document.",
        },
        say="This is the whole loop. Choose a passage, press the shortcut, and "
            "read what ByteProof would change before any of it is written.",
        beats=[
            Beat(
                caption="Your selection",
                detail="The paragraph, selected in Word. ByteProof reads the "
                       "sentence around it as well.",
                say="Start with the passage you care about. Here it is the "
                    "opening paragraph of a thesis, selected the way you would "
                    "normally select it.",
                draw=draw_selection,
                note=note("Why the whole paragraph",
                          "A single word is hard to judge on its own. The "
                          "sentences around it settle tense, agreement and tone."),
                cursor=[(0.06, 520.0, 300.0), (0.72, 800.0, 268.0)],
            ),
            Beat(
                caption="The shortcut",
                detail="⌥⌘P by default, and yours to change in settings.",
                say="Press the proofread shortcut. It reads whatever is selected, "
                    "in Word or in any other application.",
                draw=draw_shortcut,
                note=note("Yours to change",
                          "Both shortcuts can be recorded again in Settings, "
                          "under General."),
                cursor=[(0.5, 1200.0, 720.0)],
            ),
            Beat(
                caption="The suggestion card",
                detail="Three changes, each with the original, the replacement "
                       "and the reason.",
                say="The card lists each change with the original struck through "
                    "and the replacement beside it, so you are judging the edit "
                    "itself.",
                draw=draw_card,
                ring=lambda: stage.card_box(),
                cursor=[(0.1, 1180.0, 700.0), (0.55, apply_at[0], apply_at[1])],
            ),
            Beat(
                caption="Applied, as tracked changes",
                detail="The paragraph is rewritten in place, and Word records "
                       "every part of it.",
                say="Press Apply all. Every change goes into the document at "
                    "once, as a tracked change you can accept or reject.",
                draw=draw_applied,
                cue=(0.3, apply_all),
                cursor=[(0.05, apply_at[0], apply_at[1]), (0.4, 900.0, 420.0)],
                click=(0.3,),
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 03. Reading the changes
# ---------------------------------------------------------------------------


def build_review_the_changes() -> Clip:
    stage = WordStage()
    stage.make_card()
    main = MainStage(html=diff_html(), status="3 changes proposed")

    def draw_card(painter, t, beat):
        stage.draw_page(painter)
        amount = rwc.ease_out(rwc.between(t, 0.2, 0.85))
        stage.draw_card(painter, amount, (1.0 - amount) * 24.0)

    def draw_window(painter, t, beat):
        main.draw(painter, rwc.ease_out(rwc.between(t, 0.2, 0.8)))

    return Clip(
        name="03-review-the-changes",
        group="02-microsoft-word",
        card={
            "kicker": "Microsoft Word",
            "title": "Reading the changes",
            "strap": "What each row means, and where the changes go afterwards.",
            "foot": BRAND["foot"],
            "series": "Microsoft Word",
        },
        end={
            "kicker": "What this shows",
            "title": "You are the reader, not the machine",
            "strap": "Every suggestion carries the reason it was made, so you "
                     "can disagree with one.",
        },
        say="A proofread is a set of suggestions rather than a rewrite, so it is "
            "worth knowing how to read one.",
        beats=[
            Beat(
                caption="One row per change",
                detail="The original struck through, the replacement beside it, "
                       "and a dot for the kind of edit.",
                say="Each row is a single change. The original is struck "
                    "through, the replacement sits next to it, and the dot on "
                    "the left says what kind of edit it is.",
                draw=draw_card,
                note=note("The dots are a short list",
                          "Blue for a word choice, amber for grammar, red for a "
                          "spelling or a capital. Nothing else is colour coded."),
                ring=lambda: stage.card_box(),
                cursor=[(0.05, 1180.0, 720.0), (0.5, 880.0, 380.0)],
            ),
            Beat(
                caption="Why it changed",
                detail="The reason sits under the row, so a preference is not "
                       "mistaken for a rule.",
                say="The reason sits under the row. That is what lets you tell a "
                    "word choice from something the document actually needs.",
                draw=draw_card,
                note=note("Disagreeing is allowed",
                          "Close the card and nothing is written. The paragraph "
                          "is exactly as you left it."),
                ring=lambda: stage.label_rect("Plainer word"),
                cursor=[(0.1, 1160.0, 300.0), (0.45, 1180.0, 500.0)],
            ),
            Beat(
                caption="The same changes, in the app",
                detail="Read the corrected passage in one piece, then copy it "
                       "wherever it is wanted.",
                say="The same changes appear in ByteProof's own window, where "
                    "the corrected passage can be read in one piece and copied "
                    "out.",
                draw=draw_window,
                cursor=[(0.08, 1180.0, 700.0), (0.5, 1050.0, 300.0)],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 04. Reviewer comments
# ---------------------------------------------------------------------------


def build_comments_for_a_supervisor() -> Clip:
    settings = SettingsStage("general", size=(900, 620), at=(350.0, 96.0))
    settings.dialog.combo_comment.setCurrentIndex(2)
    settings.refresh()
    main = MainStage(html=comment_html(), status="3 changes and a comment")
    stage = WordStage()

    def draw_settings(painter, t, beat):
        settings.draw(painter)

    def draw_pane(painter, t, beat):
        main.draw(painter, rwc.ease_out(rwc.between(t, 0.2, 0.8)))

    def draw_balloon(painter, t, beat):
        stage.draw_page(painter, selection=(0, len(DOC_WORDS) - 1, 1.0))
        amount = rwc.ease_out(rwc.between(t, 0.3, 1.0))
        draw_comment_balloon(painter, amount)

    return Clip(
        name="04-comments-for-your-supervisor",
        group="02-microsoft-word",
        card={
            "kicker": "Microsoft Word",
            "title": "Comments for a supervisor",
            "strap": "Leave a note for whoever reads the draft next, written "
                     "against the passage it is about.",
            "foot": BRAND["foot"],
            "series": "Microsoft Word",
        },
        end={
            "kicker": "What this shows",
            "title": "The changes, with the reasoning attached",
            "strap": "The comment explains the edit, and the tracked changes "
                     "still show exactly what moved.",
        },
        say="A proofread answers a question about the writing. Reviewer comments "
            "put that answer into the document.",
        beats=[
            Beat(
                caption="Add Reviewer Comment",
                detail="Language writes about the writing. Technical explains the "
                       "change the way a supervisor would want it explained.",
                say="Reviewer comments are one setting. Language leaves a note "
                    "about the writing, and Technical adds the reasoning behind "
                    "the edit.",
                draw=draw_settings,
                enter=lambda: settings.show(settings.dialog.combo_comment),
                ring=lambda: settings.row_rect(settings.dialog.combo_comment),
                cursor=[(0.06, 1240.0, 700.0), (0.42, 1120.0, 396.0)],
            ),
            Beat(
                caption="The changes and the comment",
                detail="ByteProof's window shows the corrected passage and the "
                       "comment that goes with it.",
                say="With it on, the proofread comes back with the changes and "
                    "the comment together, so you can read the note before it "
                    "reaches anybody.",
                draw=draw_pane,
                cursor=[(0.08, 1150.0, 720.0), (0.5, 1040.0, 360.0)],
            ),
            Beat(
                caption="In Word, beside the passage",
                detail="The comment is anchored to the words it is about, so a "
                       "reader meets it in place.",
                say="The comment is anchored to the passage in Word. A "
                    "supervisor reads it next to the sentence it is about "
                    "rather than in a message somewhere else.",
                draw=draw_balloon,
                cursor=[(0.1, 640.0, 700.0), (0.55, 1080.0, 330.0)],
            ),
        ],
    )


def comment_html() -> str:
    return diff_html() + (
        "<p style='font-family:Menlo;font-size:12px;color:#15803D;"
        "font-weight:600;margin-bottom:2px'>Reviewer Comment</p>"
        "<p style='font-family:Menlo;font-size:12px;color:#57534E'>"
        "The paragraph leans on wordy connectives where a single word would "
        "carry the same meaning, which slows the argument at the point it "
        "should be moving.</p>"
    )


def draw_comment_balloon(painter, amount: float) -> None:
    """Word's margin comment, drawn as a balloon beside the page."""
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QColor, QPen
    from record_demo_videos import draw_paragraph

    if amount <= 0.02:
        return
    box = QRectF(946.0, 268.0, 500.0, 214.0)
    box.moveTop(box.top() + (1.0 - amount) * 20.0)
    painter.save()
    painter.setOpacity(amount)
    rwc.draw_shadow(painter, box, 12.0, dy=10, blur=24, alpha=40)
    painter.setPen(QPen(QColor("#e3ded1"), 1.0))
    painter.setBrush(QColor("#fffefb"))
    painter.drawRoundedRect(box, 12.0, 12.0)
    painter.setBrush(QColor("#c9a227"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(box.left(), box.top() + 20, 4.0, 46.0), 2.0, 2.0)
    painter.setPen(QPen(QColor("#ded8ca"), 1.4))
    painter.drawLine(QPointF(900.0, 330.0), QPointF(946.0, 330.0))
    painter.setPen(QColor(PRIMARY))
    painter.setFont(qfont(("Helvetica Neue", "Inter", "Arial"), 15.0))
    painter.drawText(int(box.left() + 26), int(box.top() + 44), "Reviewer comment")
    draw_paragraph(
        painter, box.left() + 26.0, box.top() + 78.0,
        "The paragraph leans on wordy connectives where a single word would "
        "carry the same meaning. The shorter forms keep the argument moving "
        "without changing what it claims.",
        qfont(("Helvetica Neue", "Inter", "Arial"), 14.0),
        "#57534e", box.width() - 52.0, leading=22.0,
    )
    painter.setPen(QColor("#a8a29b"))
    painter.setFont(qfont(("Helvetica Neue", "Inter", "Arial"), 12.0))
    painter.drawText(int(box.left() + 26), int(box.bottom() - 20),
                     "Attached to the selected paragraph")
    painter.restore()


# ---------------------------------------------------------------------------
# 05. Citations and equations
# ---------------------------------------------------------------------------

CITATION_WORDS = ["Smith", "et", "al.", "(2021)", "report", "that", "board", "independence", "improves", "reporting", "quality,", "and", "we", "utilise", "a", "range", "of", "methods", "in", "order", "to", "test", "this", "(Jones", "and", "Lee,", "2019)."]

CITATION_CHANGES = (
    ("utilise", "use", "Plainer word"),
    ("in order to", "to", "Wordy phrase"),
)


def build_keep_citations() -> Clip:
    stage = WordStage(CITATION_WORDS)
    stage.make_card(CITATION_CHANGES)

    def draw_fields(painter, t, beat):
        stage.draw_page(painter, hints=CITATION_HINTS)

    def draw_card(painter, t, beat):
        stage.draw_page(painter, hints=CITATION_HINTS)
        amount = rwc.ease_out(rwc.between(t, 0.2, 0.9))
        stage.draw_card(painter, amount, (1.0 - amount) * 24.0)

    return Clip(
        name="05-keep-citations-and-equations",
        group="02-microsoft-word",
        card={
            "kicker": "Microsoft Word",
            "title": "Citations and equations are left alone",
            "strap": "ByteProof finds the fields first, and never rewrites what "
                     "is inside one.",
            "foot": BRAND["foot"],
            "series": "Microsoft Word",
        },
        end={
            "kicker": "What this shows",
            "title": "A reference is not a phrase to be tidied",
            "strap": "Citations, cross references and equations are located "
                     "before the writing is touched, and the changes stop at "
                     "their edges.",
        },
        say="An academic paragraph is not only prose. Some of it is field codes, "
            "and those must come out of a proofread exactly as they went in.",
        beats=[
            Beat(
                caption="Where the fields are",
                detail="Word fields are found in the selection before anything "
                       "is sent to be proofread.",
                say="ByteProof reads the selection and finds the fields in it "
                    "first. An EndNote citation, a cross reference and an "
                    "equation are all fields.",
                draw=draw_fields,
                note=note("Found in the document",
                          "The field boundaries come from Word itself, not from "
                          "guessing at brackets in the text."),
                cursor=[(0.06, 700.0, 700.0), (0.45, 300.0, 320.0)],
            ),
            Beat(
                caption="Changed around them",
                detail="The suggestions are the two phrases in the sentence. "
                       "Both citations are untouched.",
                say="The suggestions are the two phrases around the citations. "
                    "The references are not reworded, reformatted or moved, "
                    "because nothing was allowed to overlap them.",
                draw=draw_card,
                ring=lambda: stage.label_rect("Plainer word"),
                cursor=[(0.06, 1160.0, 680.0), (0.5, 880.0, 400.0)],
            ),
        ],
    )


CITATION_HINTS = (
    ("Smith et al. (2021)", "#e6e3da"),
    ("(Jones and Lee, 2019)", "#e6e3da"),
)


# ---------------------------------------------------------------------------
# 06. Writing style and context
# ---------------------------------------------------------------------------


def build_choose_a_style() -> Clip:
    settings = SettingsStage("general", size=(900, 620), at=(350.0, 96.0))
    state = {"style": 0, "context": 0, "at": 0.0}

    def to_creative() -> None:
        settings.dialog.combo_style.setCurrentIndex(1)
        settings.refresh()
        state["style"] = 1

    def to_thesis() -> None:
        settings.dialog.combo_context.setCurrentIndex(2)
        settings.refresh()
        state["context"] = 1

    def draw(painter, t, beat):
        settings.draw(painter)

    return Clip(
        name="06-choose-a-writing-style",
        group="02-microsoft-word",
        card={
            "kicker": "Setting it up",
            "title": "Choosing a style and a context",
            "strap": "Two settings decide how much ByteProof changes, and what "
                     "it assumes the writing is for.",
            "foot": BRAND["foot"],
            "series": "Getting it right",
        },
        end={
            "kicker": "What this shows",
            "title": "Tell it what the writing is",
            "strap": "An email and a thesis chapter do not want the same "
                     "edits, and these two settings are where that is settled.",
        },
        say="How much ByteProof changes is a setting, not a personality. Two "
            "choices decide it.",
        beats=[
            Beat(
                caption="Editing Style",
                detail="Precise keeps your wording intact and fixes what is "
                       "wrong with it.",
                say="Precise is the default. It keeps your wording and fixes "
                    "what is wrong with it, which is what most academic writing "
                    "wants.",
                draw=draw,
                enter=lambda: settings.show(settings.dialog.combo_style),
                ring=lambda: settings.row_rect(settings.dialog.combo_style),
                cursor=[(0.06, 1240.0, 700.0), (0.4, 1100.0, 250.0)],
            ),
            Beat(
                caption="Creative rewrites more freely",
                detail="Useful for a paragraph that needs to be said differently, "
                       "not just corrected.",
                say="Creative lets it rewrite more freely. That suits a paragraph "
                    "that has to be said differently rather than merely "
                    "corrected, and it always keeps at least half of the "
                    "meaning intact.",
                draw=draw,
                cue=(0.22, to_creative),
                enter=lambda: settings.show(settings.dialog.combo_style),
                ring=lambda: settings.row_rect(settings.dialog.combo_style),
                click=(0.22,),
                cursor=[(0.1, 1100.0, 250.0), (0.45, 1100.0, 250.0)],
            ),
            Beat(
                caption="Document Context",
                detail="Email, general, thesis chapter or journal article. The "
                       "wording is judged against the kind of document it is.",
                say="Document Context does the other half. A thesis chapter, a "
                    "journal article and an email are held to different "
                    "standards of formality, and this is where that is decided.",
                draw=draw,
                cue=(0.22, to_thesis),
                enter=lambda: settings.show(settings.dialog.combo_context),
                ring=lambda: settings.row_rect(settings.dialog.combo_context),
                click=(0.22,),
                cursor=[(0.1, 1100.0, 470.0), (0.5, 1100.0, 470.0)],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 07. Which English, and how much
# ---------------------------------------------------------------------------


def build_english_and_amount() -> Clip:
    settings = SettingsStage("general", size=(900, 620), at=(350.0, 96.0))

    def to_american() -> None:
        settings.dialog.combo_spelling.setCurrentIndex(1)
        settings.refresh()

    def more_rewriting() -> None:
        settings.dialog.temp_slider.setValue(9)
        settings.refresh()

    def draw(painter, t, beat):
        settings.draw(painter)

    return Clip(
        name="07-english-and-how-much-it-changes",
        group="02-microsoft-word",
        card={
            "kicker": "Setting it up",
            "title": "Which English, and how much",
            "strap": "The spelling it expects, and how far it is allowed to go.",
            "foot": BRAND["foot"],
            "series": "Getting it right",
        },
        end={
            "kicker": "What this shows",
            "title": "Two dials worth setting once",
            "strap": "Preferred Spelling decides which English is correct, and "
                     "the slider decides how far the edits may reach.",
        },
        say="Two more settings are worth setting before the first proofread, "
            "and then leaving alone.",
        beats=[
            Beat(
                caption="Preferred Spelling",
                detail="UK, Australian and New Zealand English by default. "
                       "American spelling is one choice away.",
                say="Preferred Spelling is UK, Australian and New Zealand English "
                    "by default. American spelling is one choice away, and it "
                    "covers punctuation as well as words.",
                draw=draw,
                cue=(0.22, to_american),
                enter=lambda: settings.show(settings.dialog.combo_spelling),
                ring=lambda: settings.row_rect(settings.dialog.combo_spelling),
                click=(0.22,),
                cursor=[(0.06, 1240.0, 700.0), (0.45, 1100.0, 320.0)],
            ),
            Beat(
                caption="How much it may change",
                detail="Left is conservative, right rewrites more. The number "
                       "beside the slider is the value that is sent.",
                say="The slider decides how much it may change. To the left it "
                    "stays conservative, and to the right it rewrites more "
                    "freely. The number beside it is the value that is actually "
                    "sent with the request.",
                draw=draw,
                cue=(0.22, more_rewriting),
                enter=lambda: settings.show(settings.dialog.temp_slider),
                ring=lambda: settings.row_rect(settings.dialog.temp_slider, pad=18.0),
                note=note("It cannot loosen Creative",
                          "Creative style always asks for at least half, however "
                          "far left this slider sits."),
                click=(0.22,),
                cursor=[(0.1, 1080.0, 250.0), (0.5, 1180.0, 250.0)],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 08. Turning Live Check on
# ---------------------------------------------------------------------------


def build_turn_on_live_check() -> Clip:
    settings = SettingsStage("live", size=(900, 620), at=(350.0, 96.0))
    shown = {"apps": False}

    def show_apps() -> None:
        if not shown["apps"]:
            settings.dialog._toggle_live_apps()
            shown["apps"] = True
            settings.refresh()

    def draw(painter, t, beat):
        settings.draw(painter)

    return Clip(
        name="08-turn-on-live-check",
        group="03-live-check",
        card={
            "kicker": "Live Check",
            "title": "Suggestions while you write",
            "strap": "Select a sentence in any app and the suggestions arrive "
                     "beside it, without asking.",
            "foot": BRAND["foot"],
            "series": "Live Check",
        },
        end={
            "kicker": "What this shows",
            "title": "You choose where it runs",
            "strap": "One switch turns it on, and the list underneath decides "
                     "which applications it may appear in.",
        },
        say="Live Check is the other half of the application. Instead of asking "
            "for a proofread, you get one as you select text.",
        beats=[
            Beat(
                caption="One switch",
                detail="Off means the panel never appears, and the shortcut "
                       "still works as it always did.",
                say="This is the switch that turns it on. With it off, ByteProof "
                    "only works when you ask, and nothing appears beside your "
                    "text at all.",
                draw=draw,
                enter=lambda: settings.show(settings.dialog.chk_live_preview),
                ring=lambda: settings.row_rect(
                    settings.dialog.chk_live_preview, pad=16.0
                ),
                cursor=[(0.06, 1240.0, 700.0), (0.42, 1180.0, 290.0)],
            ),
            Beat(
                caption="The apps it runs in",
                detail="Word, Mail, Outlook, Pages, Notes, a browser, or anything "
                       "else you add.",
                say="Suggestions never run inside ByteProof itself. The list "
                    "names every application where they may appear, and one "
                    "switch turns any of them off.",
                draw=draw,
                cue=(0.25, show_apps),
                enter=lambda: settings.show(settings.dialog.live_apps_toggle_btn),
                ring=lambda: settings.row_rect(
                    settings.dialog.live_apps_toggle_btn, pad=10.0
                ),
                click=(0.25,),
                cursor=[(0.1, 1180.0, 300.0), (0.5, 1180.0, 300.0)],
            ),
            Beat(
                caption="Or leave it open",
                detail="Allow other apps keeps the panel available everywhere, "
                       "including applications released after this build.",
                say="Allow other apps keeps it available everywhere, which "
                    "covers anything released after you installed ByteProof.",
                draw=draw,
                enter=lambda: settings.show(settings.dialog.live_apps_buttons),
                cursor=[(0.08, 1180.0, 520.0), (0.45, 1120.0, 560.0)],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 09. The floating panel
# ---------------------------------------------------------------------------


def build_the_floating_panel() -> Clip:
    from src.gui import ToastNotification

    stage = MailStage()
    stage.paint_overlay()
    first_change = stage.spans()[0]

    toast = ToastNotification()
    rwc.hide_widget(toast)
    toast.complete("Applied to Mail. Press Cmd/Ctrl + Z to undo.", "success",
                   duration_ms=600000)
    toast.hide()
    toast_image = rwc.snap(toast, 18.0)
    toast_at = (float((1600 - toast_image.width()) / 2), 668.0)

    state = {"applied": False, "touched": []}

    def open_popup() -> None:
        stage.open_popup(1)

    def apply_change() -> None:
        first, last = stage.apply(1)
        state["touched"] = list(range(first, last))
        state["applied"] = True

    def draw_selection(painter, t, beat):
        stage.draw(painter, overlay=False,
                   selection=(0, len(MAIL_WORDS) - 1,
                              rwc.smooth(rwc.between(t, 0.3, 1.6))))

    def draw_underlines(painter, t, beat):
        amount = rwc.ease_out(rwc.between(t, 0.2, 0.9))
        stage.draw(painter, alpha=amount, overlay=True,
                   selection=(0, len(MAIL_WORDS) - 1, 1.0))

    def draw_popup(painter, t, beat):
        stage.draw(painter, selection=(0, len(MAIL_WORDS) - 1, 1.0)
                   if not state["applied"] else None)
        stage.draw_popup(painter, rwc.ease_out(rwc.between(t, 0.35, 0.8)))

    def draw_applied(painter, t, beat):
        fixes = {index: rwc.flash_colour(t, 0.3) for index in state["touched"]}
        stage.draw(painter, fixes=fixes)
        amount = rwc.ease_out(rwc.between(t, 0.55, 1.0))
        if amount > 0.02:
            painter.save()
            painter.setOpacity(amount)
            painter.drawImage(int(toast_at[0]), int(toast_at[1]), toast_image)
            painter.restore()

    return Clip(
        name="09-the-floating-panel",
        group="03-live-check",
        card={
            "kicker": "Live Check",
            "title": "The panel beside your text",
            "strap": "Underlined suggestions, one click to read the reason, one "
                     "click to take it.",
            "foot": BRAND["foot"],
            "series": "Live Check",
        },
        end={
            "kicker": "What this shows",
            "title": "The suggestion waits for you",
            "strap": "Nothing is replaced until you click it, and the underline "
                     "is the whole of what ByteProof adds to the page.",
        },
        say="This is what Live Check looks like while you write. Nothing has "
            "been sent anywhere and nothing has been changed.",
        beats=[
            Beat(
                caption="Select a sentence",
                detail="The same selection you would make anyway. No menu, no "
                       "shortcut, no button to press.",
                say="Select a sentence the way you normally would. There is no "
                    "shortcut to press and no menu to open.",
                draw=draw_selection,
                note=note("It waits for you to finish",
                          "Suggestions are asked for once the selection has been "
                          "still for a moment, which is a setting of its own."),
                cursor=[(0.06, 560.0, 330.0), (0.75, 900.0, 300.0)],
            ),
            Beat(
                caption="What it would change",
                detail="Each suggestion is underlined where it sits in your "
                       "sentence, so the sentence stays readable.",
                say="Each suggestion is quietly underlined where it sits in your "
                    "sentence. The writing stays readable while the suggestions "
                    "wait.",
                draw=draw_underlines,
                cursor=[(0.1, 940.0, 700.0), (0.45, first_change.rect.center().x(),
                       first_change.rect.center().y())],
            ),
            Beat(
                caption="Click one to see why",
                detail="The original, the replacement and the reason, before "
                       "anything is replaced.",
                say="Click an underline and the panel gives the original, the "
                    "replacement and the reason. That is the moment to disagree, "
                    "and closing it costs nothing.",
                draw=draw_popup,
                cue=(0.4, open_popup),
                click=(0.4,),
                cursor=[(0.1, first_change.rect.center().x(),
                         first_change.rect.center().y()),
                        (0.4, first_change.rect.center().x(),
                         first_change.rect.center().y())],
            ),
            Beat(
                caption="Taken, in place",
                detail="The sentence is rewritten where it stands, and the "
                       "underline is gone because the change is done.",
                say="Take it and the sentence is rewritten where it stands. "
                    "Undo is one keystroke, and the panel says so rather than "
                    "leaving you to wonder.",
                draw=draw_applied,
                cue=(0.3, apply_change),
                click=(0.3,),
                cursor=[(0.16, first_change.rect.center().x(),
                         first_change.rect.center().y()),
                        (0.5, 980.0, 560.0)],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 10. Undoing a change
# ---------------------------------------------------------------------------


def build_undo_a_change() -> Clip:
    from src.gui import ToastNotification

    stage = MailStage()
    stage.paint_overlay()
    applied_at = stage.spans()[0]
    first, _last = stage.apply(0)
    stage.paint_overlay()

    toast = ToastNotification()
    rwc.hide_widget(toast)
    toast.complete("Change undone", "warning", duration_ms=600000)
    toast.hide()
    toast_image = rwc.snap(toast, 18.0)
    toast_at = (float((1600 - toast_image.width()) / 2), 668.0)

    state = {"undone": False}

    def undo() -> None:
        state["undone"] = True
        stage.sheet.tokens[first:first + 1] = [rwc.Token("utilise")]
        # The suggestion exists again, so the underline has to come back with
        # it: an undo puts the suggestion where it was, not just the letters.
        stage.changes = (MAIL_CHANGES[0],) + stage.changes
        stage.paint_overlay()

    def draw_applied(painter, t, beat):
        from PyQt6.QtCore import QRectF

        fixes = {index: rwc.flash_colour(t, 0.2) for index in range(first, first + 1)}
        stage.draw(painter, fixes=None if state["undone"] else fixes)
        if state["undone"]:
            return
        pill = stage.pill_image
        at = (float(applied_at.rect.left() + 26),
              float(stage.sheet.bounds().bottom() + 26.0))
        amount = rwc.ease_out(rwc.between(t, 0.2, 0.8))
        painter.save()
        painter.setOpacity(amount)
        box = QRectF(at[0], at[1] + (1.0 - amount) * 12.0, pill.width(), pill.height())
        rwc.draw_shadow(painter, box, 12.0, dy=8, blur=20, alpha=44)
        painter.drawImage(int(box.left()), int(box.top()), pill)
        painter.restore()

    def draw_undone(painter, t, beat):
        stage.draw(painter)
        amount = rwc.ease_out(rwc.between(t, 0.2, 0.8))
        painter.save()
        painter.setOpacity(amount)
        painter.drawImage(int(toast_at[0]), int(toast_at[1]), toast_image)
        painter.restore()

    return Clip(
        name="10-undo-a-change",
        group="03-live-check",
        card={
            "kicker": "Live Check",
            "title": "Taking a change back",
            "strap": "An applied suggestion offers Undo for a few seconds, and "
                     "the keyboard shortcut works for longer than that.",
            "foot": BRAND["foot"],
            "series": "Live Check",
        },
        end={
            "kicker": "What this shows",
            "title": "Applying is not a commitment",
            "strap": "One click takes the suggestion, and one click puts your "
                     "own wording back exactly as it was.",
        },
        say="A suggestion that has been taken is not a suggestion you are stuck "
            "with.",
        beats=[
            Beat(
                caption="The change lands",
                detail="The sentence is rewritten, and Undo appears beside the "
                       "words that moved.",
                say="The sentence is rewritten where it stood, and an Undo pill "
                    "appears next to the words that moved.",
                draw=draw_applied,
                cursor=[(0.06, 980.0, 700.0), (0.4, applied_at.rect.right() + 40.0,
                       applied_at.rect.bottom() + 40.0)],
            ),
            Beat(
                caption="Undone",
                detail="Your wording is back, and the underline returns because "
                       "the suggestion has not been taken.",
                say="Press it and your wording is back, with the underline "
                    "returning because the suggestion is there again. The "
                    "keyboard shortcut does the same thing for as long as the "
                    "document is open.",
                draw=draw_undone,
                cue=(0.28, undo),
                click=(0.28,),
                cursor=[(0.1, applied_at.rect.right() + 40.0,
                         applied_at.rect.bottom() + 40.0),
                        (0.45, 900.0, 330.0)],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 11. Which apps it watches
# ---------------------------------------------------------------------------


def build_which_apps() -> Clip:
    from PyQt6.QtWidgets import QCheckBox

    settings = SettingsStage("live", size=(900, 620), at=(350.0, 96.0))
    settings.dialog._toggle_live_apps()
    settings.refresh()
    state = {"off": False}

    def row(name: str):
        listing = settings.dialog.live_apps_list
        for index in range(listing.count()):
            item = listing.item(index)
            if settings.dialog.live_app_name(item) == name:
                return item
        return None

    def switch_off(name: str) -> None:
        item = row(name)
        if item is None:
            return
        widget = settings.dialog.live_apps_list.itemWidget(item)
        check = widget.findChild(QCheckBox) if widget is not None else None
        if check is not None:
            check.setChecked(False)
        state["off"] = True
        settings.refresh()

    def draw(painter, t, beat):
        settings.draw(painter)

    return Clip(
        name="11-which-apps-it-watches",
        group="03-live-check",
        card={
            "kicker": "Live Check",
            "title": "Which apps it appears in",
            "strap": "A list of applications, each with its own switch, and a "
                     "way to add anything that is missing.",
            "foot": BRAND["foot"],
            "series": "Live Check",
        },
        end={
            "kicker": "What this shows",
            "title": "Off means never",
            "strap": "An application switched off here is never read, so a "
                     "sensitive window can be left out of the list entirely.",
        },
        say="ByteProof can appear in one application and stay out of another, "
            "and that is a list rather than a global setting.",
        beats=[
            Beat(
                caption="One switch per app",
                detail="Every listed application can be left out. The list only "
                       "decides where suggestions may appear.",
                say="Every application in this list has its own switch. Word and "
                    "Mail can be on while a chat window is off.",
                draw=draw,
                enter=lambda: settings.show(settings.dialog.live_apps_list, 20.0),
                ring=lambda: settings.row_rect(
                    settings.dialog.live_apps_list, pad=6.0
                ),
                cursor=[(0.06, 1240.0, 700.0), (0.45, 1120.0, 380.0)],
            ),
            Beat(
                caption="Turning one off",
                detail="Switched off, that application is left alone. Nothing is "
                       "read and no panel appears.",
                say="Switch one off and that application is left alone entirely. "
                    "It is not read, and it is not proofread.",
                draw=draw,
                cue=(0.25, lambda: switch_off("Pages")),
                click=(0.25,),
                cursor=[(0.1, 1120.0, 520.0), (0.45, 1120.0, 520.0)],
            ),
            Beat(
                caption="Adding an app",
                detail="Add App picks from what is installed, so an application "
                       "this build has never heard of still works.",
                say="Add App picks from what is installed on this machine. An "
                    "application this build has never heard of can still be "
                    "added, and it behaves like any other.",
                draw=draw,
                ring=lambda: settings.row_rect(
                    settings.dialog.live_add_app_btn, pad=10.0
                ),
                cursor=[(0.08, 1120.0, 600.0), (0.45, 1100.0, 600.0)],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 12. When it waits, and when it fires
# ---------------------------------------------------------------------------


def build_when_it_waits() -> Clip:
    settings = SettingsStage("live", size=(900, 620), at=(350.0, 96.0))

    def slower() -> None:
        settings.dialog.live_delay_slider.setValue(900)
        settings.refresh()

    def fewer_words() -> None:
        settings.dialog.live_min_words_spin.setValue(5)
        settings.refresh()

    def draw(painter, t, beat):
        settings.draw(painter)

    return Clip(
        name="12-when-it-waits",
        group="03-live-check",
        card={
            "kicker": "Live Check",
            "title": "When it waits, and when it fires",
            "strap": "Four settings decide how eager the panel is, and each one "
                     "exists to stop it interrupting you.",
            "foot": BRAND["foot"],
            "series": "Live Check",
        },
        end={
            "kicker": "What this shows",
            "title": "Tuned to the way you work",
            "strap": "A slower delay and a higher word count are what make the "
                     "panel feel like a colleague rather than a prompt.",
        },
        say="Live Check has to decide when not to speak. These four settings are "
            "how that is decided.",
        beats=[
            Beat(
                caption="Wait after selecting",
                detail="How long the selection must stay still. Lower feels "
                       "faster, higher never fires mid-selection.",
                say="The delay is how long the selection has to stay unchanged "
                    "before anything is asked for. Lower feels faster, and "
                    "higher never fires while you are still choosing your words.",
                draw=draw,
                cue=(0.22, slower),
                enter=lambda: settings.show(settings.dialog.live_delay_slider),
                ring=lambda: settings.row_rect(settings.dialog.live_delay_slider,
                                               pad=16.0),
                click=(0.22,),
                cursor=[(0.06, 1240.0, 700.0), (0.5, 1120.0, 300.0)],
            ),
            Beat(
                caption="How much text",
                detail="A minimum, so a stray word or two is ignored, and a "
                       "maximum, so a whole section is left alone.",
                say="The two ends of the range matter as much. A short selection "
                    "is ignored, because a word or two is not enough to judge, "
                    "and a very long one is left alone because a whole section in "
                    "one panel is rarely what you wanted.",
                draw=draw,
                cue=(0.22, fewer_words),
                enter=lambda: settings.show(settings.dialog.live_min_words_spin),
                ring=lambda: settings.row_rect(settings.dialog.live_min_words_spin,
                                               pad=16.0),
                note=note("About four thousand characters",
                          "That is six to eight paragraphs, which is as much as "
                          "anyone reads in a side panel."),
                click=(0.22,),
                cursor=[(0.1, 1120.0, 380.0), (0.5, 1120.0, 380.0)],
            ),
            Beat(
                caption="Only while the pointer stays",
                detail="Move the pointer away and the suggestion waits, which is "
                       "how a passing selection is ignored.",
                say="The last one is the quietest. While the pointer stays at the "
                    "selection the panel appears, and the moment you move away it "
                    "waits, so a selection made in passing is never interrupted.",
                draw=draw,
                enter=lambda: settings.show(settings.dialog.chk_live_pointer_near),
                ring=lambda: settings.row_rect(
                    settings.dialog.chk_live_pointer_near, pad=16.0
                ),
                cursor=[(0.08, 1120.0, 500.0), (0.45, 1120.0, 500.0)],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 13. Anywhere else
# ---------------------------------------------------------------------------


def build_polish_in_any_app() -> Clip:
    from src.gui import ToastNotification

    stage = MailStage()
    toast = ToastNotification()
    rwc.hide_widget(toast)
    toast.complete("Applied to Mail. Press Cmd/Ctrl + Z to undo.", "success",
                   duration_ms=600000)
    toast.hide()
    toast_image = rwc.snap(toast, 18.0)
    state = {"applied": False, "touched": []}

    def polish() -> None:
        first, last = stage.sheet.index_of("in order to")
        stage.sheet.replace(first, last, "to")
        state["touched"] = list(range(first, first + 1))
        state["applied"] = True

    def draw_selection(painter, t, beat):
        stage.draw(painter, overlay=False,
                   selection=(0, len(MAIL_WORDS) - 1,
                              rwc.smooth(rwc.between(t, 0.3, 1.5))))

    def draw_shortcut(painter, t, beat):

        stage.draw(painter, overlay=False,
                   selection=(0, len(MAIL_WORDS) - 1, 1.0))
        bounds = stage.sheet.bounds()
        painter.save()
        painter.setOpacity(rwc.between(t, 0.1, 0.6))
        scale = 0.9 + 0.1 * rwc.ease_out(rwc.between(t, 0.1, 0.7))
        rwc.draw_key_chip(painter, bounds.left() + 300.0, bounds.bottom() + 32.0,
                          ["⌥", "⌘", "P"], scale)
        painter.restore()

    def draw_applied(painter, t, beat):
        fixes = {index: rwc.flash_colour(t, 0.3) for index in state["touched"]}
        stage.draw(painter, overlay=False, fixes=fixes)
        amount = rwc.ease_out(rwc.between(t, 0.6, 1.1))
        if amount > 0.02:
            painter.save()
            painter.setOpacity(amount)
            painter.drawImage(int((1600 - toast_image.width()) / 2), 668,
                              toast_image)
            painter.restore()

    return Clip(
        name="13-polish-in-any-app",
        group="04-anywhere-else",
        card={
            "kicker": "Anywhere else",
            "title": "Polishing a selection anywhere",
            "strap": "Email, a browser, a chat window: select it, press the "
                     "shortcut, and the polished sentence goes back.",
            "foot": BRAND["foot"],
            "series": "Anywhere else",
        },
        end={
            "kicker": "What this shows",
            "title": "Between two modes, not two applications",
            "strap": "Word gets tracked changes. Everything else gets the "
                     "finished sentence, in place, with undo a keystroke away.",
        },
        say="Word is only one of the places people write. The same shortcut "
            "works in the rest of them.",
        beats=[
            Beat(
                caption="A draft in a mail window",
                detail="Anything with selectable text: Mail, Outlook, a browser, "
                       "a chat window, an editor.",
                say="This is a draft in a mail window. ByteProof reads the "
                    "paragraph and the two sentences around it, so the tone "
                    "matches the rest of the message.",
                draw=draw_selection,
                note=note("No Word required",
                          "The same keystroke reads a selection in any "
                          "application, with or without Word installed."),
                cursor=[(0.06, 560.0, 330.0), (0.75, 900.0, 300.0)],
            ),
            Beat(
                caption="Press the shortcut",
                detail="⌥⌘P by default. The application comes forward, the text "
                       "is read, and the answer comes back.",
                say="Press the proofread shortcut. The window it was pressed in "
                    "comes forward while the text is read, so you can see what is "
                    "being worked on.",
                draw=draw_shortcut,
                cursor=[(0.5, 1120.0, 700.0)],
            ),
            Beat(
                caption="The finished sentence goes back",
                detail="Not a suggestion to copy: the selection is replaced, and "
                       "the answer says so.",
                say="The finished sentence goes back over the selection. There is "
                    "nothing to copy and paste, and the message says so in plain "
                    "words rather than leaving you to check.",
                draw=draw_applied,
                cue=(0.3, polish),
                click=(0.3,),
                cursor=[(0.14, 900.0, 330.0), (0.5, 1000.0, 620.0)],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 14. Running it offline
# ---------------------------------------------------------------------------


def build_run_it_offline() -> Clip:
    from src import gui as gui_mod
    from src.local_model import get_model

    model_id = "qwen3-1.7b"
    model = get_model(model_id)
    total = int(model["size_bytes"])
    # The finished state has to survive the page asking the disk whether the
    # model is there. Nothing was downloaded - the progress bar is staged - so
    # the answer is staged with it, and only once the download has finished.
    # Answering "installed" from the first frame would give away the ending of
    # a film whose whole subject is the download.
    real_is_installed = gui_mod.is_model_installed
    state = {"started": False, "done": False}
    gui_mod.is_model_installed = lambda mid: (
        state["done"] and mid == model_id
    ) or real_is_installed(mid)

    settings = SettingsStage("local", size=(900, 620), at=(350.0, 96.0))

    def card_widget(name: str):
        widgets = settings.dialog.local_model_cards.get(model_id) or {}
        return widgets.get(name)

    def start_download() -> None:
        state["started"] = True
        settings.dialog._local_downloading = model_id
        for other, widgets in settings.dialog.local_model_cards.items():
            widgets["download"].setEnabled(other != model_id)
            if other == model_id:
                widgets["download"].setText("Downloading…")
        settings.dialog.local_status_label.setObjectName("SettingsValue")
        settings.dialog.local_status_label.setText(f"Preparing {model['name']}…")
        settings.refresh()

    def finish() -> None:
        state["done"] = True
        settings.dialog._on_local_download_done(model_id)
        settings.refresh()

    def restore() -> None:
        gui_mod.is_model_installed = real_is_installed

    def draw(painter, t, beat):
        if state["started"] and not state["done"]:
            done = int(total * min(1.0, rwc.between(t, 0.9, 3.4)))
            if done < total:
                settings.dialog._on_local_download_progress(done, total, "Downloading")
            else:
                settings.dialog._on_local_download_progress(total, total, "Verifying")
            settings.refresh()
        settings.draw(painter)

    return Clip(
        name="14-run-it-offline",
        group="05-where-the-ai-runs",
        card={
            "kicker": "Local AI",
            "title": "Running it offline",
            "strap": "Download one model and the proofreading happens on this "
                     "computer, with no key and no connection.",
            "foot": BRAND["foot"],
            "series": "Where it runs",
        },
        end={
            "kicker": "What this shows",
            "title": "Nothing leaves the machine",
            "strap": "With a local model, the words being proofread are never "
                     "sent anywhere, because there is nowhere to send them.",
        },
        say="The proofreading has to happen somewhere. One of the answers is on "
            "this computer.",
        beats=[
            Beat(
                caption="A model, downloaded once",
                detail="A small model for quick corrections, larger ones for "
                       "heavy rewriting. Each is downloaded once and kept.",
                say="These are the models that can run here. A small one is "
                    "enough for corrections, and a larger one does better on "
                    "heavily rewritten prose. Each is downloaded once.",
                draw=draw,
                note=note("No key, no account",
                          "The local models do not need an API key, an account "
                          "or a connection after the download."),
                cursor=[(0.06, 1240.0, 700.0), (0.45, 1080.0, 330.0)],
            ),
            Beat(
                caption="The download",
                detail="One download, then it is yours. The progress is real: "
                       "the size and the checksum come from the model list.",
                say="The download is the only time it needs the network. What "
                    "arrives is checked against a published checksum before it "
                    "is used.",
                draw=draw,
                cue=(0.2, start_download),
                ring=lambda: settings.row_rect(card_widget("download"), pad=12.0),
                enter=lambda: settings.show(card_widget("download"), 60.0),
                click=(0.2,),
                cursor=[(0.1, 1080.0, 330.0), (0.35, 1080.0, 330.0)],
            ),
            Beat(
                caption="Ready, and offline after this",
                detail="The model is listed as installed, and Live Check can run "
                       "on it from then on.",
                say="Once it is installed, Live Check can use it for every "
                    "suggestion. From this point the network can be off "
                    "permanently and the proofreading still works.",
                draw=draw,
                cue=(0.16, finish),
                click=(0.16,),
                enter=lambda: settings.show(card_widget("download"), 60.0),
                cursor=[(0.08, 1080.0, 400.0), (0.45, 1080.0, 400.0)],
            ),
        ],
        cleanup=restore,
    )


# ---------------------------------------------------------------------------
# 15. Bringing your own key
# ---------------------------------------------------------------------------


PROVIDER = "DeepSeek"


def build_bring_your_own_key() -> Clip:
    from PyQt6.QtWidgets import QDialog, QLineEdit

    settings = SettingsStage("connect", size=(900, 620), at=(350.0, 96.0))
    state = {"dialog": None, "image": None}

    def choose() -> None:
        settings.dialog.set_active_provider(PROVIDER)
        settings.refresh()

    def open_configure() -> None:
        # The dialog is modal and the film is not. Letting exec() return
        # straight away builds it and stops: the block underneath only runs
        # when a person presses Save, so nothing is written anywhere.
        original = QDialog.exec
        QDialog.exec = lambda self: 0
        try:
            settings.dialog.open_provider_settings(PROVIDER)
        finally:
            QDialog.exec = original
        dialogs = settings.dialog.findChildren(QDialog)
        if not dialogs:
            return
        dialog = dialogs[-1]
        rwc.hide_widget(dialog)
        dialog.resize(440, 340)
        fields = dialog.findChildren(QLineEdit)
        for field in fields:
            if field.echoMode() == QLineEdit.EchoMode.Password:
                field.setText("sk-00000000000000000000000000000000")
                break
        rwc.flush()
        state["dialog"] = dialog
        state["image"] = rwc.snap(dialog, 12.0)

    def draw_page(painter, t, beat):
        settings.draw(painter)

    def draw_dialog(painter, t, beat):
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor

        settings.draw(painter)
        image = state["image"]
        if image is None:
            return
        veil = QColor(0, 0, 0, 90)
        painter.save()
        painter.setPen(0)
        painter.setBrush(veil)
        painter.drawRect(QRectF(0, 0, 1600, 900))
        painter.restore()
        amount = rwc.ease_out(rwc.between(t, 0.25, 0.8))
        box = QRectF((1600 - image.width()) / 2,
                     150 + (1.0 - amount) * 24.0, image.width(), image.height())
        painter.save()
        painter.setOpacity(amount)
        rwc.draw_shadow(painter, box, 14.0, dy=18, blur=34, alpha=52)
        painter.drawImage(int(box.left()), int(box.top()), image)
        painter.restore()

    return Clip(
        name="15-bring-your-own-key",
        group="05-where-the-ai-runs",
        card={
            "kicker": "Connect",
            "title": "Bringing your own key",
            "strap": "DeepSeek, OpenAI, Anthropic, Google, xAI, Groq or "
                     "Perplexity, with the key you already have.",
            "foot": BRAND["foot"],
            "series": "Where it runs",
        },
        end={
            "kicker": "What this shows",
            "title": "Your key, your account, your bill",
            "strap": "ByteProof does not resell the model. You bring the key, "
                     "you keep the account, and you can change your mind later.",
        },
        say="The other answer is a model you already pay for. ByteProof can use "
            "the key you hold rather than one of its own.",
        beats=[
            Beat(
                caption="One provider at a time",
                detail="Local models are marked LOCAL and free ones FREE. The "
                       "active provider is the one that answers.",
                say="One provider answers at a time, and the list says which of "
                    "them are free and which run on this machine.",
                draw=draw_page,
                note=note("Switching is instant",
                          "Changing provider does not change the document, the "
                          "settings or anything already applied."),
                cursor=[(0.06, 1240.0, 700.0), (0.45, 1050.0, 300.0)],
            ),
            Beat(
                caption="Choosing one",
                detail="The chosen provider becomes the active one and keeps "
                       "that place until it is changed.",
                say="Choose one and it becomes the provider for every proofread "
                    "from then on. Nothing else about the application changes.",
                draw=draw_page,
                cue=(0.25, choose),
                click=(0.25,),
                cursor=[(0.1, 1050.0, 300.0), (0.45, 1050.0, 300.0)],
            ),
            Beat(
                caption="Where the key goes",
                detail="Configure takes the key and the model name, and the "
                       "connection can be tested before it is saved.",
                say="Configure is where the key goes. Paste it once, test the "
                    "connection before you rely on it, and ByteProof keeps it on "
                    "this machine rather than anywhere else.",
                draw=draw_dialog,
                cue=(0.22, open_configure),
                click=(0.22,),
                cursor=[(0.12, 1050.0, 420.0), (0.55, 820.0, 480.0)],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 16. The trial
# ---------------------------------------------------------------------------


def _trial_license():
    from src import gui as gui_mod

    originals = {
        name: getattr(gui_mod, name)
        for name in ("get_license_info", "ensure_trial_started", "get_trial_status")
    }
    gui_mod.get_license_info = lambda: {"status": "unlicensed"}
    gui_mod.ensure_trial_started = lambda: 0.0
    gui_mod.get_trial_status = lambda _ts: {
        "in_trial": True, "days_left": 7, "trial_expired": False,
    }

    def restore() -> None:
        for name, original in originals.items():
            setattr(gui_mod, name, original)

    return restore


def build_the_free_trial() -> Clip:
    restore = _trial_license()
    settings = SettingsStage("license", size=(900, 620), at=(350.0, 96.0))

    def draw(painter, t, beat):
        settings.draw(painter)

    return Clip(
        name="16-the-free-trial",
        group="06-licence",
        card={
            "kicker": "Licence",
            "title": "The first two weeks",
            "strap": "Everything works before you pay anything, including the "
                     "parts that need a key.",
            "foot": BRAND["foot"],
            "series": "Licence and updates",
        },
        end={
            "kicker": "What this shows",
            "title": "Trial first, decision later",
            "strap": "The trial is the whole application. Nothing is held back "
                     "until it ends, and nothing has to be entered to start it.",
        },
        say="A new copy of ByteProof starts with the whole application available "
            "for a fortnight.",
        beats=[
            Beat(
                caption="What is left of the trial",
                detail="The licence page says how long is left rather than "
                       "hiding it until something stops working.",
                say="The licence page counts the trial down in days. There is no "
                    "account to make and no card to enter, because the trial is "
                    "kept on this computer.",
                draw=draw,
                ring=lambda: settings.row_rect(settings.dialog.status_frame, pad=8.0),
                cursor=[(0.06, 1240.0, 700.0), (0.45, 1050.0, 330.0)],
            ),
            Beat(
                caption="What a licence adds",
                detail="Reviewer comments and the cloud providers need one. The "
                       "local models and the proofreading do not.",
                say="A licence is what unlocks reviewer comments and the cloud "
                    "providers. Proofreading on a local model keeps working "
                    "either way.",
                draw=draw,
                ring=lambda: settings.row_rect(settings.dialog.btn_buy, pad=12.0),
                cursor=[(0.1, 1050.0, 420.0), (0.45, 1180.0, 430.0)],
            ),
        ],
        cleanup=restore,
    )


# ---------------------------------------------------------------------------
# 17. Activating a licence
# ---------------------------------------------------------------------------


def build_activate_a_licence() -> Clip:
    from PyQt6.QtWidgets import QApplication, QInputDialog, QLabel, QLineEdit

    from src import gui as gui_mod
    from src.gui import activation_prompt

    restore = _trial_license()
    key = "polar_xxxxxxxxxxxxxxxxxxxxxxxx"
    holder: dict = {}
    settings = SettingsStage("license", size=(900, 620), at=(350.0, 96.0))

    def open_prompt() -> None:
        from PyQt6.QtGui import QColor

        title, label = activation_prompt()
        prompt = QInputDialog()
        prompt.setWindowTitle(title)
        prompt.setLabelText(label)
        prompt.setInputMode(QInputDialog.InputMode.TextInput)
        prompt.setOkButtonText("Activate")
        prompt.setCancelButtonText("Cancel")
        prompt.setTextValue(key)
        # The prompt is three sentences long, and QInputDialog gives a plain
        # label one line unless it is told otherwise: without this the text is
        # clipped and the field overlaps the buttons. The size then comes from
        # the layout rather than from a number, or Qt stretches the field past
        # the edge of the dialog.
        for label in prompt.findChildren(QLabel):
            label.setWordWrap(True)
            label.setMinimumWidth(600)
            label.setMinimumHeight(label.heightForWidth(600))
        for field in prompt.findChildren(QLineEdit):
            field.setMinimumWidth(600)
        prompt.adjustSize()
        hint = prompt.sizeHint()
        prompt.setFixedSize(hint)
        rwc.hide_widget(prompt)
        rwc.flush()
        holder["prompt"] = prompt
        # A plain QDialog paints no background of its own, so without this the
        # panel comes out transparent and the settings page shows straight
        # through the middle of it.
        holder["image"] = rwc.snap(prompt, 12.0, background=QColor("#FFFFFF"))
        QApplication.processEvents()

    def activate() -> None:
        prompt = holder.get("prompt")
        if prompt is not None:
            prompt.close()
        holder["image"] = None
        gui_mod.get_license_info = lambda: {
            "status": "licensed",
            "provider": "polar",
            "key_display": "…7F2C",
            "email": "",
            "expiry": None,
            "activated_at": "",
        }
        settings.dialog._refresh_license_tab()
        settings.refresh()

    def draw(painter, t, beat):
        from PyQt6.QtCore import QRectF

        settings.draw(painter)
        image = holder.get("image")
        prompt = holder.get("prompt")
        if image is None or prompt is None:
            return
        box = QRectF((1600 - prompt.width()) / 2.0,
                     (900 - prompt.height()) / 2.0 - 40.0,
                     prompt.width(), prompt.height())
        amount = rwc.ease_out(rwc.between(t, 0.3, 0.75))
        painter.save()
        painter.setOpacity(amount)
        rwc.draw_shadow(painter, box, 12.0, dy=14, blur=30, alpha=46)
        painter.drawImage(int(box.left()), int(box.top()), image)
        painter.restore()

    return Clip(
        name="17-activate-a-licence",
        group="06-licence",
        card={
            "kicker": "Licence",
            "title": "Activating a licence",
            "strap": "Paste the key from the receipt. There is no account to "
                     "make and nothing to install again.",
            "foot": BRAND["foot"],
            "series": "Licence and updates",
        },
        end={
            "kicker": "What this shows",
            "title": "One key, on this computer",
            "strap": "The key is checked, stored locally, and shown back to you "
                     "with all but the last characters hidden.",
        },
        say="Activation is a single step, and it does not involve an account.",
        beats=[
            Beat(
                caption="Paste the key",
                detail="The key comes from the purchase receipt or the licence "
                       "email, and it is checked before anything is stored.",
                say="Paste the key from the receipt. ByteProof checks it with the "
                    "licence service before anything is written, so a mistyped "
                    "key never leaves the licence page half applied.",
                draw=draw,
                cue=(0.22, open_prompt),
                ring=lambda: settings.row_rect(settings.dialog.btn_auto_activate,
                                               pad=12.0),
                click=(0.22,),
                cursor=[(0.08, 1240.0, 700.0), (0.4, 1180.0, 430.0)],
            ),
            Beat(
                caption="Licensed",
                detail="The page shows the last characters of the key and the "
                       "state of the licence, and the features unlock.",
                say="The page then shows the last few characters of the key and "
                    "the state of the licence. Reviewer comments and the cloud "
                    "providers are available from that moment.",
                draw=draw,
                cue=(0.22, activate),
                click=(0.22,),
                cursor=[(0.1, 1180.0, 430.0), (0.5, 1000.0, 300.0)],
            ),
        ],
        cleanup=restore,
    )


# ---------------------------------------------------------------------------
# 18. Updates
# ---------------------------------------------------------------------------


def build_stay_up_to_date() -> Clip:
    settings = SettingsStage("updates", size=(900, 620), at=(350.0, 96.0))

    def draw(painter, t, beat):
        settings.draw(painter)

    return Clip(
        name="18-stay-up-to-date",
        group="06-licence",
        card={
            "kicker": "Updates",
            "title": "Staying on the current build",
            "strap": "Which version this is, where it came from, and how to ask "
                     "for a newer one.",
            "foot": BRAND["foot"],
            "series": "Licence and updates",
        },
        end={
            "kicker": "What this shows",
            "title": "Nothing installs itself",
            "strap": "An update is offered and described. It is downloaded and "
                     "installed when you say so, never quietly.",
        },
        say="Updates are offered rather than imposed, and this page is where the "
            "offer appears.",
        beats=[
            Beat(
                caption="Which build this is",
                detail="The version number, and whether it is a beta or a "
                       "release. Both matter when reporting a problem.",
                say="The page names the version and whether it is a beta or a "
                    "release. That is the first thing worth knowing when "
                    "something needs reporting.",
                draw=draw,
                ring=lambda: settings.row_rect(settings.dialog.version_label, pad=14.0),
                cursor=[(0.06, 1240.0, 700.0), (0.45, 1080.0, 300.0)],
            ),
            Beat(
                caption="Asking for a newer one",
                detail="A check is a request, not an installation. Nothing is "
                       "replaced until the download is finished and accepted.",
                say="Check for Updates asks whether there is a newer build. If "
                    "there is, it is described and offered, and it is installed "
                    "only when you accept it.",
                draw=draw,
                ring=lambda: settings.row_rect(settings.dialog.update_check_btn,
                                               pad=12.0),
                note=note("A check is quiet",
                          "It sends the current version number and nothing else. "
                          "No document, no text and no account."),
                cursor=[(0.08, 1080.0, 380.0), (0.45, 1150.0, 380.0)],
            ),
        ],
    )


FILMS = [
    film("01-what-byteproof-does", "01-getting-started", build_what_it_does),
    film("02-proofread-a-selection", "02-microsoft-word", build_proofread_a_selection),
    film("03-review-the-changes", "02-microsoft-word", build_review_the_changes),
    film("04-comments-for-your-supervisor", "02-microsoft-word", build_comments_for_a_supervisor),
    film("05-keep-citations-and-equations", "02-microsoft-word", build_keep_citations),
    film("06-choose-a-writing-style", "02-microsoft-word", build_choose_a_style),
    film("07-english-and-how-much-it-changes", "02-microsoft-word", build_english_and_amount),
    film("08-turn-on-live-check", "03-live-check", build_turn_on_live_check),
    film("09-the-floating-panel", "03-live-check", build_the_floating_panel),
    film("10-undo-a-change", "03-live-check", build_undo_a_change),
    film("11-which-apps-it-watches", "03-live-check", build_which_apps),
    film("12-when-it-waits", "03-live-check", build_when_it_waits),
    film("13-polish-in-any-app", "04-anywhere-else", build_polish_in_any_app),
    film("14-run-it-offline", "05-where-the-ai-runs", build_run_it_offline),
    film("15-bring-your-own-key", "05-where-the-ai-runs", build_bring_your_own_key),
    film("16-the-free-trial", "06-licence", build_the_free_trial),
    film("17-activate-a-licence", "06-licence", build_activate_a_licence),
    film("18-stay-up-to-date", "06-licence", build_stay_up_to_date),
]
