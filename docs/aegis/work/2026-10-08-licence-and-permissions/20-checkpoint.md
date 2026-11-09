# Safari editing, and the licence/permission re-entry — Checkpoint

## What was wrong

### 1. The app could not post keystrokes, so applies failed (the "Safari" bug)

The 15:04 log is unambiguous: ByteProof read Safari's field, set the AX
selection (the "selected text" line proves the range took), posted Command-V -
and the field never changed. Both the live apply (`ax_replace_range`) and the
manual polish (`mac_replace`) failed the same way, while the same two paths had
succeeded at 12:52, 13:04, 13:07 and 13:41 with the previous build.

The difference was the Accessibility grant. Each install at 14:50 and 15:00
left the freshly started app answering `LIVE PERMISSION: trusted=False`
(14:51:32, 15:00:51); the owner re-granted at ~14:52 and 15:03:36 - and the
applies that followed still failed, because macOS gives a *running* process
its answer for reading but keeps refusing its *posted keystrokes* until it is
started again.

### 2. Why the grant kept disappearing

Measured on this machine:

* `ditto dist/ByteProof.app /Applications/ByteProof.app` (copy into the
  existing bundle, the workflow the beta docs recommend) -> next launch logs
  `trusted=False`.
* stage beside it, rename the old bundle away, rename the new one in (what the
  app's own DMG updater does) -> next launch logs `trusted=True`.

So the local beta workflow was breaking the grant, not the DMG updates. A
hand-added entry in System Settings is also tied to the build that was there
when it was added; an entry created through macOS's own prompt is tied to the
app's identity instead, which is why the re-grant advice used to be "toggle it
off and on" and needed repeating.

### 3. Why the licence kept needing the key

The fingerprint hashed `uuid.getnode()` (the machine's **first network
interface**), `platform.node()` (the **hostname**) and the hardware UUID
together. This machine has fourteen virtual interfaces with regenerated MACs
and a DHCP-friendly hostname; changing any of them changed the fingerprint.
The stored licence then failed `_validated_license_data()`, the app fell to
free mode, and the owner typed the key again - registering yet another
activation. The service counted **four activations for one computer**, and the
`device_count` in the stored licence had grown the same way.

## What changed

* **The fingerprint is stable** (`src/licensing.py`): it hashes the OS hardware
  identity (IOPlatformUUID on macOS, MachineGuid on Windows, `/etc/machine-id`
  elsewhere) and falls back to the old mixture only when no hardware identity
  exists.
* **An existing licence survives the change**: `_validated_license_data()`
  accepts the old value as well, and `activation.migrate_to_stable_fingerprint()`
  (run at startup validation, where the network is already being used)
  re-activates against the stable identity and releases the old seat, so the
  customer neither types anything nor pays a seat. Verified live on the
  owner's machine: the stored licence now binds the stable value, the stable
  fingerprint validates, and the old one answers `not_activated`.
* **Installing a beta keeps the permission**: new
  `scripts/install_local_beta.sh` does the updater's atomic swap, verifies the
  signature and reopens the app - and says so in the output.
* **A missing grant is asked for properly** (`src/gui.py`): the status row's
  action is now *Enable*, which triggers macOS's own prompt
  (`AXIsProcessTrustedWithOptions`) as well as opening the pane, and the
  post-update hint says to remove a hand-added entry so the durable one can be
  created.
* **A grant that arrives mid-run restarts the app once**: the app watches the
  live status, and on `no_permission` -> `ready` shows "Accessibility
  restored — restarting ByteProof so editing works…" and reopens itself, which
  is what makes applies work again.

## Deliberately left alone

- A licence the customer released (portal or another computer) still stops
  working: `not_activated` still deletes the local copy and tells the user, so
  moving a licence between computers keeps working. Only a licence bound to
  *this* machine's old fingerprint is moved silently.
- The update flow itself (DMG + detached helper) is unchanged; it was already
  the swap shape that keeps the grant.

## What the owner should see now

- Updates (DMG or `./scripts/install_local_beta.sh`) do not ask for the
  licence key or the Accessibility permission again.
- If macOS does drop the grant, the app asks once through the system prompt
  and restarts itself after it is allowed.
- Editing in Safari (and everywhere else) works as it did before.
