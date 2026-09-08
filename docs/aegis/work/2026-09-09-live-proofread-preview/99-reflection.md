# Live Proofread Preview — Reflection

The beta meets the requested end state: live selection-triggered previews,
pink-red dashed underlines, a track-changes hover popup, one-click apply, a
General-settings toggle, and token-efficiency guards, all verified in real
apps and shipped as a signed, notarized installer.

The largest unplanned work was the macOS overlay layer: masked translucent
windows do not composite with the `Tool` flag when the app has a normal
window, and global NSEvent monitors do not observe synthetic mouse moves. The
fix (no Tool flag, local-coordinate mask, and a session-level Quartz event tap)
is now covered by a render test and the live campaign.

Known limits: Word shows a cursor-anchored card instead of a per-word hover
popup because Word exposes no character bounds through AX; Mail live preview
depends on the compose body exposing AX selected text; Pages needs the user to
grant automation permission. Windows live preview remains out of scope for
this beta.
