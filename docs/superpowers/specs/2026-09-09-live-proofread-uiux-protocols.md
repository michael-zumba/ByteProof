# UI/UX Polish Protocols — Live Suggestions

Approved by the owner on 2026-09-09. Implemented one protocol group per
build cycle so each step can be verified live before the next.

## Implementation order (owner-approved)

1. P2.1 Loading pill + P2.2 Undo after apply
2. P1 Onboarding & trust (readiness card + permission guidance)
3. P3 Option B: shared design tokens + diff-view reuse in the main window
4. P2.4 Reason color dots, then P4.1 status chip, P4.3 quit flow
5. Backlog: P2.3 suggestion dismiss, P2.5 panel position memory, P4.4
   per-app filter

---

## Protocol 1 — Onboarding & Trust

Goal: eliminate silent failure when Accessibility permission is off,
revoked by a reinstall, or flaky.

- Readiness card in the main window: `Live: on · Accessibility · provider`.
- "Open System Settings" deep link when permission is missing.
- One-line re-grant hint after app updates.
- "Test now" button probing the frontmost app (reuses diagnostics).

## Protocol 2 — Suggestion panel: expectation, undo, dismiss

- P2.1 Loading pill: a small "Checking…" pill at the selection the moment
  a provider call starts; hidden when the panel appears or the call ends.
- P2.2 Undo: after a successful apply, an "Undo" pill (10 s lifetime)
  restores the previous text at the recorded absolute range.
- P2.3 Dismiss: per-row "don't suggest this again" (session dictionary).
- P2.4 Reason color dots: Spelling=red, Grammar=amber, Word choice=blue.
- P2.5 Panel position memory per display after dragging.

## Protocol 3 — One visual language

- Option B approved: shared design-token module (spacing, radii, focus,
  hover/pressed states) used by the main window and the live panel.
- Reuse the panel's diff renderer (struck red → green replacement with
  dimmed context) in the main window's corrected-text view.

## Protocol 4 — Status transparency & quit friction

- P4.1 Live status chip (tray tooltip + main window footer).
- P4.2 Provider + latency in the panel title tooltip.
- P4.3 Quit flow: close = tray silently, Quit quits instantly, remember
  confirmation choice.
- P4.4 Optional per-app filter in Settings.

## Safety rules

- Every change is test-covered (tests/test_live_preview.py) and shipped as
  a signed, notarized installer on the beta branch.
- No engine/timing/apply-path regressions: the existing 80+ tests plus the
  smoke suite must stay green each round.

### P3.1 Settings surface (owner-approved 2026-09-18, shipped 2.1.1-beta.13)

The settings dialog was the last surface still carrying its own look: 36
distinct hex colours in 202 places, 128 inline setStyleSheet calls, 11 nested
group boxes, and status text living on whichever page happened to own the
message. ByteMail's settings window - same ByteMind palette, one token file -
is the reference the owner asked for. The owner fixed the scope: restyle only,
keep Save/Cancel, 7 pages, no IA change.

- **Tokens.** src/ui_theme.py gains the SHELL_* group (the ByteMind cream and
  green ByteMail renders) plus settings_stylesheet(), which is the dialog's
  whole look in one string. The live suggestion panel keeps its cool blue: two
  surfaces, two moods, one brand.
- **Type scale.** Page title 17 semibold, page blurb 12 muted, section micro
  heading 11 demi-bold upper-case with 0.8px tracking, row title 13, row helper
  12 muted, hint 11 faint. The tracking comes from the font, not the sheet:
  Qt has no letter-spacing style property, so a rule for it silently does
  nothing.
- **Sidebar.** App mark and "ByteProof / Settings" identity block, an icon on
  every row (rendered in both normal and selected colours so it reads on the
  green pill), selected row = solid green pill, cream rail.
- **Pages.** Flat sections - a micro heading over its rows - instead of nested
  group boxes, and one row shape everywhere: title and one-line helper on the
  left, control right-aligned, hairline between rows, every row starting at the
  same left edge. Checkboxes are switches that hold state only; the sentence
  that used to be the label is the row's title and helper. Clicking anywhere on
  a switch row flips it.
- **Feedback.** One footer line, left of Save/Cancel (_set_status,
  info/success/error). The page-level status labels were retired.
- **Unchanged on purpose.** 7 pages, page mapping, the attribute names the
  tests rely on, Save/Cancel semantics, the hotkey-conflict prompt and the
  defaults flow.

#### P3.1b Control, card and list system (2.1.1-beta.14)

The pages converted in P3.1 were consistent; the cards, lists and controls
around them were not - nine different font sizes (9, 10, 11, 12, 13, 14, 16,
20, 30px), a card title at 12px bold on one page and 13px medium on the next,
badges at 9px, a 34px app-list row next to a 40px switch row, and thirteen
buttons carrying a stylesheet of their own. One scale and one shape per role
now:

| Role | Size / weight | Used for |
| --- | --- | --- |
| SettingsTitle | 17 semibold | page title |
| SettingsDisplay | 22 semibold | the version number |
| SettingsHero | 15 semibold | company name, licence state, temperature readout |
| SettingsCardTitle | 13 semibold | card headings |
| SettingsRowTitle | 13 | row and list-item names |
| SettingsValue | 12 secondary | values, contact lines |
| SettingsRowHelper | 12 muted | the line under a row title |
| SettingsHint | 11 faint | notes, meta lines |
| SettingsStatus | 11 | state lines, coloured by kind |
| SettingsSectionLabel | 11 demi-bold, 0.8px tracking, upper-case | section headings |
| SettingsBadge | 10 demi-bold pill | LOCAL / FREE / model tags |

- **Buttons** - one base shape (7px 14px padding, 16px min height, 8px radius,
  12px/500 label) with four roles: PrimaryBtn (filled), DangerBtn (ghost red),
  SmallBtn (disclosure: Show Apps, Show Triggers, Add App, Check for Updates,
  Test Connection, Restore default settings) and LinkBtn (Clean Up Unused
  Files). A disabled primary stays green (Primary-300), so "Active" reads as a
  state rather than as a broken button.
- **Selects and fields** - one shape (6px 10px padding, 17px min height, 8px
  radius), 190px minimum width, the shared chevron arrow, 26px popup rows.
- **Cards** - ProviderCard, LicenseCard, SettingsCard and SettingsCallout all
  pad 16/14; trigger cards pad 14/12 and show selection through a property
  rather than a stylesheet.
- **Lists** - the Live Check app list uses the same 40px row height as a switch
  row, so the two read as one family.
- **State colours** - tone(label, kind) sets a kind property the sheet colours;
  no widget writes a hex of its own.

#### P3.1c The window sheet stops at the window (2.1.1-beta.15)

The settings dialog is a child of the main window, so the main window's
stylesheet applied to every widget inside it. Forty-eight of its selectors were
written bare - QWidget, QComboBox, QCheckBox, QPushButton, QListWidget,
QScrollBar and the rest - which is why the dialog did not render as designed:

- a bare QWidget rule set font-size 13px on every dialog widget the roles did
  not explicitly cover, so sizes drifted wherever a role was missing;
- a bare QCheckBox::indicator rule set width 20px, height 20px, a 2px border
  and a white background, drawing a bordered box under the 40x23 switch art;
- a bare QComboBox rule set padding 10px 40px 10px 14px, a 12px radius and a
  12x8 down-arrow, competing with the dialog's own field metrics and chevron.

Rules now:

- **The window sheet is scoped to #RootPanel.** It styles the window's own
  panel and cannot reach a dialog; only QMainWindow, QMenu, QToolTip and
  QMessageBox stay global, because they are top-level windows rather than
  children. A test asserts the allowlist, so the leak cannot come back.
- **The dialog sheet owns every state it uses**: a base rule (QDialog,
  QDialog *) for widgets no role covers; the switch indicator pinned to
  image-only with no border and no fill, including its hover and focus states;
  every combo state (hover, focus, on, disabled, drop-down, popup row, popup
  selection); pressed and disabled buttons; thin quiet scrollbars.
- Opening Settings from the running app is now measured to be identical to
  opening it standalone: same control heights, same type scale, same switch
  art.

#### P3.1d Owner review round: numbers, triggers and the window structure (2.1.1-beta.16)

Two defects the owner found by using the build, plus what the pass turned up:

- **Number fields hid their own value.** The word/character fields were pinned
  to 64px and 92px, and the sheet reserved 24px on the right for the arrows on
  top of the space Qt already reserves for them, so the digits had almost
  nothing left. Fixed widths are gone: the sheet gives number fields a 88px
  minimum and no double reservation. An assertion now checks, for every field
  on every page, that the visible text area is wider than the widest value the
  field can hold.
- **Automation read as loose parts.** A page paragraph, a floating count label,
  a floating button, a list with no height and a trailing hint line. It is now
  the same structure as every other page: the blurb lives in the page header,
  the count sits inside the row it describes, the hint is that row's tooltip,
  and the trigger list opens at a 240px minimum with 6px item spacing.
- **The Add Trigger window** was a QFormLayout with Label: prefixes and a stray
  hint line; it now uses the same three rows (Match type, Value, Context) with
  their own tooltips and a right-aligned action footer.
- **The font stack named -apple-system**, which Qt cannot resolve, so every
  label fell back silently (and the log said so). The family rule is gone: the
  platform UI font is used, as ByteMail does.
- **The licence buttons stretched to the full page width** (737px for a 150px
  label), because each was dropped straight into the page column. They are a
  left-aligned action row now, with the destructive action on its own line.

#### P3.1e Platform constraint: the menu bar menu belongs to the app (2.1.1-beta.17)

Not a design rule, a survival one. macOS 27 opens a status item's menu through
NSSceneStatusItem; Qt's observer for the "menu began tracking" notification then
asks the current event for its clickCount, and in that path the current event is
a scene action rather than a mouse event. The ObjC assertion behind that aborts
the process before the menu is on screen (crash reports 2026-09-18 20:27 and
20:28). The app therefore keeps its own QMenu and opens it from the activated
signal at the pointer; AppKit is never asked to raise a status item menu. Do not
put setContextMenu back on macOS without testing a real click.
