# ByteProof Agent Mandate

ByteProof is a proprietary desktop proofreading app (Python 3.13 + PyQt6) for
macOS and Windows. The current workstream is the live proofread preview beta:
selection-triggered, token-efficient suggestions with dashed underlines, a
hover popup, and one-click apply.

Authority boundaries: the human owner approves design changes, system
permissions, and release/integration decisions. Agents may implement, test,
build, and install the beta. They must not click system permission dialogs,
spend unapproved money, or push to main without approval.

Durable state lives in `docs/aegis/` (intent, checkpoints, evidence) and
`docs/superpowers/` (specs, plans). Feature branch: `codex/live-proofread-preview`.

Versioning rule: every update, however minor, bumps `APP_VERSION` in
`src/settings.py` (`1.9.0-beta.N`, incrementing `N`) and mirrors it in
`version_info.txt`, so testers can always tell they are on the latest build.
