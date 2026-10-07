"""The Word demonstration films: the real proofread, on page two of the paper.

These are not the composited films in ``video_clips.py``. Each of these is
recorded from the screen while ByteProof proofreads a paragraph in Microsoft
Word, so what a viewer sees is the edit landing as a tracked change, at the
speed the model actually took. ``record_word_demo.py`` films them.

The films share one shape: change one setting in ByteProof's own Settings
window, select one paragraph of page two, press the proofread shortcut, and
watch the tracked changes arrive. Each film changes a different setting, so
the set reads as one argument told five ways rather than five feature tours.

Everything a body does is verified by ``word_demo_driver.py``: the selection is
read back, the dropdown is read back, the revisions are counted. A take that
does not match its script fails rather than writing a film of the wrong thing.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent

# The frame, in screen points. 1440x810 is 16:9 and clears the Dock, which
# sits on the left edge of this screen and cannot be hidden from a film safely.
CAPTURE = {"x": 60, "y": 136, "width": 1440, "height": 810}

# Where each window stands while the camera runs. Word is wide enough to show
# the page and the comment margin; ByteProof takes the width its own layout
# allows, because 640 points is its minimum.
WINDOWS = {
    "word": (60, 25, 1430, 921),
    "app": (860, 95, 640, 810),
    "settings": (760, 140, 740, 790),
}

TARGET = {
    "workspace": str(PROJECT),
    "app_name": "ByteProof",
    "process": "ByteProof",
    "capture": CAPTURE,
    "windows": WINDOWS,
    "manuscript": str(
        Path.home()
        / "Python Projects/Personal/ByteMind Project/testing document"
        / "manuscript_immersive_tech_disclosure.docx"
    ),
    "brand": {
        "name": "ByteProof",
        "subtitle": "Proofreading in Word",
        "logo": str(PROJECT / "logo" / "logo.png"),
        "site": "bytemind.co.nz",
    },
}

# The paragraphs of page two a film can select, by the words each one opens
# with. Offsets are worked out at take time from Word's own text, so inserting
# a word in the manuscript moves the selection with it.
PARAGRAPHS = {
    "metaverse": "The metaverse has been promoted as a convergence",
    "tenk": "The 10-K annual report mandated by the US",
    "dictionaries": "Prior work on emerging-technology disclosure",
    "measure": "I apply this measure to the 25 US-listed",
}

GROUPS = {
    "01-context": "Editing to the right standard",
    "02-spelling": "Spelling",
    "03-freedom": "Editing freedom",
    "04-comments": "Reviewer comments",
}

SUBJECTS = {
    "paper": "manuscript_immersive_tech_disclosure.docx, page 2",
}

CLIPS: list[dict] = []


def clip(**spec) -> None:
    """Register a film, with its closing card read out as the last line."""
    closing = (spec.get("end") or {}).get("strap", "")
    if closing:
        spec["say"] = list(spec["say"]) + [closing]
    CLIPS.append(spec)


# ===========================================================================
# 01 - the standard an edit is held to
# ===========================================================================

clip(
    name="01-academic-journal",
    group="01-context",
    subject="paper",
    steps=7,
    # The owner asked for films 1 and 3 in the voice film 5 was cast with. A
    # voice set here wins over the seeded casting for this clip alone, so
    # re-recording the whole set brings every voice back the same.
    voice="af_heart",
    card={
        "kicker": "Document Context",
        "title": "Proofread for the journal it's going to",
        "strap": "One paragraph from page two, edited to the standard of a "
                 "top-tier journal submission.",
        "foot": "<b>manuscript_immersive_tech_disclosure.docx</b> · page 2",
    },
    end={
        "kicker": "What this shows",
        "title": "The standard is a setting",
        "strap": "The same paragraph reads differently when the context "
                 "changes, and the citations stay exactly where they are.",
    },
    say=[
        ("Page two of a real manuscript, and one paragraph of it about to be "
        "proofread."),
        ("Document Context is the standard the edit is held to. This manuscript "
        "is aiming at a top-tier journal, so that's the one it's set to. We can "
        "all dream."),
        ("The change applies to the next proofread. Nothing else in the settings "
        "has to move."),
        ("On page two, one paragraph: the bit that explains why a 10-K is worth "
        "measuring at all."),
        ("Press the proofread shortcut. ByteProof reads the selection, and the "
        "text around it, then edits against the journal standard."),
        ("A licensed copy gets two passes: a language review first, then the "
        "edit itself. That is what the timer is counting."),
        ("The paragraph is measured against the journal standard, and the "
        "citations are marked as untouchable. Nothing is written until the "
        "whole edit is ready."),
        ("The edits land as tracked changes in Word. Deletions and insertions "
        "are both in the document, and both can be rejected. No pressure."),
    ],
    body="""
await prepare(word=True, app=True)
await titles()
await sayStep('Document Context',
              'The standard the edit is held to: general, thesis, or a top-tier journal.')
await settingsRow('Document Context')
await hold()
await sayStep('Academic Journal (Top-Tier)',
              'Set it, save, and the next proofread uses it.')
await settingsChoose('Document Context', 'Academic Journal (Top-Tier)')
await settingsSave()
await hideAppWindow()
await wordFront()
await selectParagraph('tenk')
await hold()
await sayStep('One paragraph of page two',
              'The paragraph is selected where it lives, citations and all.')
await hold()
await sayStep('Proofread the selection',
              'The shortcut is the same one that works in any app.')
await proofread()
await hold()
await sayStep('Two passes, one edit',
              'The language review runs first. Its reading is what keeps the '
              'edits consistent.')
await hold()
await sayStep('Nothing lands until it is whole',
              'The paragraph is read in full before any of it is written.')
await hold()
await waitForEdits()
await sayStep('Tracked changes',
              'Every edit is a Word revision: accept it, or reject it.')
await hold(2.0)
await credits()
""",
)

# ===========================================================================
# 02 - spelling
# ===========================================================================

clip(
    name="02-preferred-spelling",
    group="02-spelling",
    subject="paper",
    steps=6,
    card={
        "kicker": "Preferred Spelling",
        "title": "New Zealand English, or US English",
        "strap": "The spelling the journal wants, applied to the paragraph you "
                 "select and nothing else.",
        "foot": "<b>manuscript_immersive_tech_disclosure.docx</b> · page 2",
    },
    end={
        "kicker": "What this shows",
        "title": "One setting, one spelling",
        "strap": "The word that had to change changed. The paragraph's "
                 "argument, and its citations, did not.",
    },
    say=[
        ("Journals ask for one spelling and not the other, and the manuscript "
        "has to match."),
        ("Preferred Spelling is where that's set. The manuscript is written in "
        "New Zealand English and the journal wants US spelling."),
        "Saved. The next proofread uses it.",
        ("This paragraph, on page two, uses the word visualisation. That's the "
        "word an American journal will mark."),
        ("The proofread checks the same paragraph again: spelling first, then "
        "the grammar and clarity it always checks."),
        ("Nothing lands until the whole edit is ready, so the document is never "
        "half-corrected. The spelling is the first thing checked."),
        ("The edit arrives as a tracked change, so you can see exactly which "
        "letters moved before you accept it."),
    ],
    body="""
await prepare(word=True, app=True)
await titles()
await sayStep('Preferred Spelling',
              'New Zealand English, or US English. This manuscript needs US.')
await settingsRow('Preferred Spelling')
await hold()
await sayStep('US English',
              'Saved, and applied to the next proofread.')
await settingsChoose('Preferred Spelling', 'US English')
await settingsSave()
await hideAppWindow()
await wordFront()
await selectParagraph('dictionaries')
await hold()
await sayStep('A paragraph with a British spelling in it',
              'Visualisation, in the middle of the paragraph.')
await hold()
await sayStep('Proofread the selection',
              'The same shortcut, on the same paragraph.')
await proofread()
await hold()
await sayStep('Nothing lands until it is whole',
              'Spelling first, then the grammar and clarity it always checks.')
await hold()
await waitForEdits()
await sayStep('Tracked change',
              'Visualisation becomes visualization, and the revision shows it.')
await hold(2.0)
await credits()
""",
)

# ===========================================================================
# 03 - editing freedom
# ===========================================================================

clip(
    name="03-editing-freedom",
    group="03-freedom",
    subject="paper",
    steps=6,
    voice="af_heart",
    card={
        "kicker": "Editing freedom",
        "title": "How far the edit can move",
        "strap": "From keeping your sentences to reshaping them, and the "
                 "tracked changes show the difference.",
        "foot": "<b>manuscript_immersive_tech_disclosure.docx</b> · page 2",
    },
    end={
        "kicker": "What this shows",
        "title": "The slider is the licence to rewrite",
        "strap": "Low keeps your wording. Higher lets a sentence be rebuilt "
                 "when the rebuilt one is clearer.",
    },
    say=[
        ("A light edit and a rewrite are different jobs, and the same paragraph "
        "can want either."),
        ("Editing freedom is how far ByteProof may move from your wording. At "
        "the low end it keeps your sentences and fixes what is broken in them."),
        ("Pushed up, it is allowed to rebuild a sentence when the structure is "
        "the problem. That is the difference between tidying and renovating."),
        ("The same paragraph as the first film, selected again, so the setting "
        "is the only thing that differs."),
        ("It reads the paragraph the same way. What changes is how much it is "
        "allowed to do about what it reads."),
        ("Nothing lands until the whole edit is ready, which is why the document "
        "sits still for a moment. Worth the wait."),
        ("The tracked changes are the proof: this sentence is shorter than the "
        "one it replaces, and it says the same thing."),
    ],
    body="""
await prepare(word=True, app=True)
await titles()
await sayStep('Editing freedom',
              'Conservative, or free to rewrite. The number is the setting.')
await settingsRow('Editing freedom')
await hold()
await sayStep('Turned up',
              'From 0.2 to 0.7: more room to rebuild a sentence.')
await settingsTemperature(0.7)
await settingsSave()
await hideAppWindow()
await wordFront()
await selectParagraph('metaverse')
await hold()
await sayStep('The same paragraph',
              'Page two, selected as before, so the difference is the setting.')
await hold()
await sayStep('Proofread the selection',
              'The same shortcut, the same paragraph, a freer editor.')
await proofread()
await hold()
await sayStep('Nothing lands until it is whole',
              'At this freedom it may rebuild a sentence, not only repair it.')
await hold()
await waitForEdits()
await sayStep('What the freedom bought',
              'Fewer words, same claim, every change tracked.')
await hold(2.0)
await credits()
""",
)

# ===========================================================================
# 04 - a language note
# ===========================================================================

clip(
    name="04-language-comment",
    group="04-comments",
    subject="paper",
    steps=6,
    card={
        "kicker": "Add Reviewer Comment",
        "title": "An edit, and the note explaining it",
        "strap": "A Word comment that says why the paragraph was changed, "
                 "written for the person who has to sign it off.",
        "foot": "<b>manuscript_immersive_tech_disclosure.docx</b> · page 2",
    },
    end={
        "kicker": "What this shows",
        "title": "The note lands in the margin",
        "strap": "Comments are for the writer. The tracked changes are for the "
                 "text. Both arrive in the same pass.",
    },
    say=[
        ("An edit answers a question about the writing. Sometimes that answer "
        "belongs in the document."),
        ("Add Reviewer Comment puts a note in Word beside the tracked changes. "
        "Language leaves a note about the writing itself."),
        ("Saved. The note is added to the next proofread, and the edits don't "
        "change."),
        "This paragraph closes the method section on page two.",
        ("The proofread runs exactly as it did before. Turning the note on adds "
        "a second thing rather than changing the edit."),
        ("The edits and the note are written in one pass, so the margin fills in "
        "at the same moment the text changes."),
        ("The corrections arrive as tracked changes, and the note arrives as a "
        "Word comment in the margin."),
    ],
    body="""
await prepare(word=True, app=True)
await titles()
await sayStep('Add Reviewer Comment',
              'Language, or the technical reviewer. This film asks for Language.')
await settingsRow('Add Reviewer Comment')
await hold()
await sayStep('Language',
              'A note about the writing, saved for the next proofread.')
await settingsChoose('Add Reviewer Comment', 'Language')
await settingsSave()
await hideAppWindow()
await wordFront()
await selectParagraph('measure')
await hold()
await sayStep('A paragraph on page two',
              'The close of the method section, selected where it sits.')
await hold()
await sayStep('Proofread the selection',
              'The same shortcut, with the note switched on.')
await proofread()
await hold()
await sayStep('One pass, two things',
              'The note is written with the edit, not after it.')
await hold()
await waitForEdits(expect_comment=True)
await sayStep('The edit, and the comment',
              'Tracked changes in the text, a Word comment in the margin.')
await hold(2.0)
await credits()
""",
)

# ===========================================================================
# 05 - a reviewer's note
# ===========================================================================

clip(
    name="05-technical-comment",
    group="04-comments",
    subject="paper",
    steps=6,
    card={
        "kicker": "Add Reviewer Comment",
        "title": "The reviewer's note, not the editor's",
        "strap": "The same proofread, with a comment that argues the case "
                 "rather than explaining the grammar.",
        "foot": "<b>manuscript_immersive_tech_disclosure.docx</b> · page 2",
    },
    end={
        "kicker": "What this shows",
        "title": "Two kinds of note",
        "strap": "Language explains the writing. Technical argues the "
                 "paragraph, which is what a methods reviewer asks for.",
    },
    say=[
        ("A methods reviewer asks a different question from a proofreader: not "
        "whether it's clear, but whether it holds."),
        ("Add Reviewer Comment has a second setting, Technical. It keeps the "
        "same edits and writes a different note."),
        ("Saved. The next proofread reads the paragraph the way a reviewer "
        "would."),
        "The paragraph that argues why the 10-K is worth measuring, on page two.",
        ("The proofread is the same one from the first film. The note is the "
        "only thing that changes."),
        ("The reviewer's note is written from the paragraph it just read, so it "
        "argues the method rather than the grammar."),
        ("The tracked changes land in the text, and the reviewer's note lands in "
        "the margin beside them."),
    ],
    body="""
await prepare(word=True, app=True)
await titles()
await sayStep('Add Reviewer Comment',
              'Technical writes the note a reviewer would write.')
await settingsRow('Add Reviewer Comment')
await hold()
await sayStep('Technical (Reviewer)',
              'The same edits, a note that argues the method.')
await settingsChoose('Add Reviewer Comment', 'Technical (Reviewer)')
await settingsSave()
await hideAppWindow()
await wordFront()
await selectParagraph('tenk')
await hold()
await sayStep('The paragraph that argues the method',
              'Page two again, citations kept out of the edit.')
await hold()
await sayStep('Proofread the selection',
              'Same shortcut, same paragraph, the reviewer note switched on.')
await proofread()
await hold()
await sayStep('A reviewer, reading along',
              'The note is written from the paragraph, after the edit.')
await hold()
await waitForEdits(expect_comment=True)
await sayStep('The edit, and the reviewer',
              'Tracked changes in the text, a Word comment in the margin.')
await hold(2.0)
await credits()
""",
)


def check_clips() -> None:
    """Refuse a plan that cannot be filmed, before anything is started."""
    names: set[str] = set()
    for film in CLIPS:
        where = film["name"]
        if where in names:
            raise SystemExit(f"{where}: two clips share a name")
        names.add(where)
        if film["group"] not in GROUPS:
            raise SystemExit(f"{where}: no such group as {film['group']}")
        steps = film["body"].count("await sayStep(")
        if steps != film["steps"]:
            raise SystemExit(
                f"{where}: {steps} spoken steps in the body, {film['steps']} "
                "in the clip"
            )
        if len(film["say"]) != steps + 2:
            raise SystemExit(
                f"{where}: {len(film['say'])} narration lines for {steps} "
                "steps and two cards"
            )
        for line in film["say"]:
            if "—" in line or " – " in line:
                raise SystemExit(f"{where}: an em dash is not allowed")
        if "{paragraph}" in film["body"]:
            raise SystemExit(f"{where}: a body still has a placeholder in it")


check_clips()
