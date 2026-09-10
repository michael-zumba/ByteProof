# ByteProof Agent Mandate

ByteProof is a proprietary desktop proofreading app (Python 3.13 + PyQt6) for
macOS and Windows. The current workstream is the live proofread preview:
selection-triggered, token-efficient AI suggestions with a floating panel
and one-click apply (released as ByteProof 2.0).

Authority boundaries: the human owner approves design changes, system
permissions, and release/integration decisions. Agents may implement, test,
build, and install the beta. They must not click system permission dialogs,
spend unapproved money, or push to main without approval.

Release policy (owner-mandated): every change ships as a beta build first
(`1.9.0-beta.N` / future beta series, installed locally for the owner to
test). Official releases (version bump, GitHub release, website update
feed) happen ONLY when the owner explicitly instructs to pack and release.
Never auto-release.

Durable state lives in `docs/aegis/` (intent, checkpoints, evidence) and
`docs/superpowers/` (specs, plans).

Versioning rule: every update, however minor, bumps `APP_VERSION` in
`src/settings.py` and mirrors it in `version_info.txt`, so testers can
always tell they are on the latest build.
