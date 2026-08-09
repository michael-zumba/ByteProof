# ByteProof activation server

Small FastAPI service that turns Stripe payments into signed, machine-bound
ByteProof license keys, and enforces the 2-computer limit (same model as
VoiceInk/Polar's activate/deactivate/validate flow).

Endpoints:
- `POST /api/byteproof/stripe-webhook` — records `checkout.session.completed`
- `POST /api/byteproof/activate` — verifies payment (Stripe session or email),
  registers this computer, and returns a permanent machine-bound key
- `POST /api/byteproof/deactivate` — frees this computer's slot
- `POST /api/byteproof/validate` — reports whether this computer is registered
- `GET /health`

## One-click deploy (Render)

The repository already contains `render.yaml` and `Dockerfile`:

1. Push this repo to GitHub (or use the existing `michael-zumba/ByteProof`).
2. On [Render](https://render.com), choose **New → Blueprint**, select the
   repo, and let it create the `byteproof-api` service.
3. Render asks for three secret environment variables:
   - `STRIPE_SECRET_KEY`
   - `STRIPE_WEBHOOK_SECRET`
   - `BYTEPROOF_LICENSE_PRIVATE_KEY`
4. For the private key value, open the local `tools/generate_license.py`, copy
   the text between `-----BEGIN PRIVATE KEY-----` and
   `-----END PRIVATE KEY-----` (including both lines), and paste it in. Render
   stores it as a secret; it is never committed to the repo.

Note: the free Render tier does not support persistent disks. Payment and
license records live in the container filesystem, which resets on redeploys.
That is fine for testing. For production, either upgrade the service (a small
persistent disk) or move the registry to a managed Postgres database.

## Local development

1. `pip install -r server/requirements.txt`
2. Set `BYTEPROOF_GENERATOR_PATH` to your local `tools/generate_license.py`,
   or set `BYTEPROOF_LICENSE_PRIVATE_KEY` to the PEM text.
3. Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`.
4. Run: `uvicorn server.activation_api:app --host 0.0.0.0 --port 8000`

## Stripe configuration

- Add a webhook endpoint pointing to
  `https://<your-domain>/api/byteproof/stripe-webhook` and subscribe to
  `checkout.session.completed`.
- Current deployment (free plan): the app points at
  `https://byteproof-api.onrender.com`. If you later move to a custom domain
  (`api.bytemind.co.nz`), update `ACTIVATION_API_URL` in `src/activation.py`
  and the Stripe webhook endpoint URL, then rebuild the app.
- Set the payment link's success URL to
  `byteproof://activate?session={CHECKOUT_SESSION_ID}` (Stripe replaces the
  placeholder). When ByteProof is installed, checkout returns straight into
  the app and activates this computer automatically.
- Send buyers a fulfilment email with a fallback link:
  `byteproof://activate?email=<buyer-email>`.

## Data model

- `payments.json` — emails with confirmed Stripe payments
- `licenses.json` — issued keys, stored per email per machine fingerprint
  (max 2 machines per license; deactivation frees a slot)

## Security notes

- Keys are RSA-signed with your private key (loaded from the
  `BYTEPROOF_LICENSE_PRIVATE_KEY` secret) and bound to the requesting machine
  fingerprint. Copying a key to a different computer fails local
  signature/machine validation.
- The server registry is the source of truth for the 2-computer limit; a
  third computer is rejected with a "device limit reached" error.
- The desktop app also stores the license payload in the macOS Keychain or
  Windows Credential Manager, re-verifies the signature every time it loads,
  and offers "Deactivate This Computer" to free a slot.

## Activation email

When checkout completes, the webhook sends a backup activation email with a
one-click link. Configure SMTP in Render:

- `BYTEPROOF_SMTP_HOST` — e.g. `smtp.gmail.com`
- `BYTEPROOF_SMTP_PORT` — default `587`
- `BYTEPROOF_SMTP_USER` — the sending account
- `BYTEPROOF_SMTP_PASSWORD` — an app password, never the account password
- `BYTEPROOF_SMTP_FROM` — the From address (defaults to SMTP_USER)
- `BYTEPROOF_SMTP_TLS` — `true` (default) or `false`

If SMTP is not configured, the webhook still records the payment and
in-app activation still works; the email is simply skipped and logged.

Keep `DATA_DIR` private and back it up. Rebuilding it from scratch would
require buyers to activate again (keys are regenerable, but old keys would
stop validating if the private key changes).
