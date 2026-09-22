# Proofreading prompt scope and consistency — Task Intent

## Requested outcome

The owner noticed strange suggestions when proofreading an academic manuscript
with the "Academic Journal (Top-Tier)" context, and asked for an investigation
of what could cause conflicts or a decline in quality, followed by fixes.

## Goal

The prompts stop asking for two incompatible things at once: the document
context never licences rewriting inside a minimal-change contract, the
author's claims, hedging, numbers and technical terms are protected on every
path, and the Document Context setting means the same thing wherever text is
edited.

## Success evidence

- The regressions found in the review are reproduced before the change and
  clear after it, against the real provider.
- Claim protection holds in the batch path and in live preview; genuine errors
  are still corrected (the preview corpus stays green).
- Prompt-path tests cover the context plumbing, the single-output contract and
  the claim-protection invariant; the full suite passes.
- The beta is built, installed and carries the new prompts.

## Stop condition

- `done`: the live probes and the suite pass, and the owner has the beta.
- `needs-verification`: prompt text changed without a live re-check.

## Non-goals

- Rewriting the product's editing philosophy: Precise stays conservative,
  Creative stays ambitious, and the em-dash ban stays.
- Tuning how the local model behaves: the probes ran against the configured
  cloud provider, which is what the owner uses.
