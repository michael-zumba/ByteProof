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

CI rule: macOS gates every release; the Windows suite runs the same tests
informationally because a blocked test takes the hosted Windows runner down
with it (its job, step and cancellation timers stop landing, which once held a
finished release for an hour with no output). The Windows *packaging* job is
still a hard gate. Never let a test file run bare on Windows: use
`scripts/run_tests_ci.py`, which streams output and kills a stalled process
tree, and keep `BYTEPROOF_CI_PROGRESS=1` so the log names the test it was on.

Versioning rule: every update, however minor, bumps `APP_VERSION` in
`src/settings.py` and mirrors it in `version_info.txt`, so testers can
always tell they are on the latest build. `scripts/check_version.py` verifies
the two agree (CI runs it); `tools/bump_version.py` updates both and skips the
public update feed for pre-releases. Name a beta after the next release line
(`2.0.2-beta.1`), never after a version that is already public
(`2.0.1-beta.N` sorts below the released `2.0.1`).

Licensing rule (do not regress): **no public value may unlock the app.**
`DEVELOPER_EMAILS` ships empty; developer access requires explicit local
configuration on the machine — `scripts/dev_access.py add <email>` writes
`dev-access.json` in the support folder (or set `BYTEPROOF_DEV_EMAILS`).
Customers activate with Polar keys only, and `byteproof://` links must stay
confirmed by the user before they activate anything.
