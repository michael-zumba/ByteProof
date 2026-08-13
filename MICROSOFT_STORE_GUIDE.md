# Publish ByteProof to the Microsoft Store

**Why:** the Microsoft Store is free to join (registration fees were removed
for individuals in 2025 and companies in May 2026), and Microsoft signs the
package during certification. Store installs get **no SmartScreen warning** —
that fixes the "Windows protected your PC" problem without buying a
code-signing certificate.

The MSIX packaging pipeline is already built. What's left is a few one-time
steps in Microsoft's Partner Center, which only you can do because they need
your Microsoft account and identity verification.

---

## 1. Create your free developer account (about 10–30 minutes)

1. Go to **https://storedeveloper.microsoft.com** — this exact entry point
   matters. Other paths (Partner Center, Visual Studio) show the legacy paid
   flow.
2. Click **Get started for free**.
3. Choose **Individual developer**.
4. Sign in with a personal Microsoft account, or create one.
5. Complete identity verification with a government-issued ID and a selfie
   (scanned on your phone).
6. Finish your profile and click **Go to Partner Center dashboard**.

> **Individual vs Company:** an Individual account publishes under your own
> name and cannot be changed to a Company account later. If ByteMind Ltd is
> registered and you want the company name on the Store listing, choose the
> Company flow instead (also free, but needs business verification).

## 2. Create the app and copy its Store identity (about 10 minutes)

1. In Partner Center, open **Apps and games**.
2. Click **New product** and reserve the name **ByteProof**.
3. Open your product, then go to **Product management → Product identity**.
4. Copy two values:
   - **Package/Identity/Name** — looks something like `12345ByteProof`
   - **Package/Identity/Publisher** — looks like `CN=6F1A2B3C-...`
5. Open `packaging/windows/msix-config.json` in this repo and set:
   - `identity_name` to the Name value
   - `publisher` to the Publisher value (exactly as shown, including `CN=`)
6. Commit the change.

The build signs the MSIX with a matching self-signed certificate
automatically. The Store replaces that signature during certification, so no
paid certificate is needed.

## 3. Build the MSIX package

The release pipeline creates the MSIX automatically:

- **From your Mac:** `./scripts/release.sh 1.6.4 "release notes"` builds the
  Windows zip **and** `ByteProof_Installer_x64.msix`, then attaches both to the
  GitHub Release.
- **Manual test:** GitHub → Actions → **Build ByteProof Windows Installer** →
  **Run workflow** → download the `ByteProof-Windows` artifact.

If the MSIX is missing, the config still has placeholder values — check the
workflow log for the message telling you to update `msix-config.json`.

## 4. Prepare the Store listing

You'll need:

- **Description** — reuse the text from the ByteProof website/README.
- **At least one screenshot** — PNG, 1366×768 or larger (1920×1080 is ideal).
  Take real screenshots of ByteProof running on Windows.
- **Logo** — `packaging/windows/listing/StoreLogo300x300.png` is already
  generated from your app logo.
- **Privacy policy URL** — required by the Store. ByteProof sends document
  content to AI providers when a user connects their own key, and the
  activation server handles licensing, so you need a privacy policy page on
  bytemind.co.nz before submitting.
- **Pricing** — set the app to **Free**. ByteProof's $35 NZD license and
  trial are handled inside the app, not through Store billing.

## 5. Submit for certification

1. Partner Center → your product → **Packages** → upload
   `ByteProof_Installer_x64.msix`.
2. Confirm device families: **Desktop** (Windows 10/11).
3. Complete the Store listing fields and any age-rating questionnaire.
4. Click **Submit for certification**. Review typically takes 1–3 business
   days.

## 6. After approval

1. Test on a clean Windows machine: install from the Store — there should be
   no SmartScreen warning at all.
2. Copy your product page URL (usually
   `https://apps.microsoft.com/detail/<product-id>`).
3. Update the website and README to point Windows users at the Store.
4. Follow-up task for later: ByteProof's in-app updater currently points all
   users at the GitHub ZIP. Store users should update through the Store
   instead — that needs a small app change to detect a Store installation and
   skip the built-in updater.

**ByteProof Store details (2026-08-14):** Store ID `9NHQWLVWCFTX`; deep link
will be `https://apps.microsoft.com/detail/9NHQWLVWCFTX` once the product is
live.

## Troubleshooting

| Problem | Fix |
|---|---|
| Store rejects the package: publisher doesn't match | Copy `Publisher` from Partner Center **Product identity** exactly (including `CN=`), update `msix-config.json`, rebuild. The build creates a matching certificate automatically. |
| MSIX wasn't produced in the workflow | `msix-config.json` still has `REPLACE_...` values, or the Windows SDK wasn't available (release still works, MSIX is skipped with a log message). |
| Package identity can't be changed later | The identity is locked once the app is created. Double-check the values before your first submission; fix the config and rebuild before submitting. |
| Certification feedback | Read the report in Partner Center, fix, rebuild, resubmit. The most common issues are missing screenshots, a missing privacy policy URL, and capability/age-rating questions. |
