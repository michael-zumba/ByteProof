# Proofreading prompt scope and consistency — Checkpoint

## What was wrong

1. The journal context was substituted *inside* the "Correction Scope
   (Precise Mode)" list, so its "remove filler" and "report findings plainly
   without overclaiming" advice competed with the minimal-change rules it was
   nested in. Measured against the real provider: a fine word was rewritten
   ("These findings are important" became "findings matter", 2 of 3 runs under
   the journal context, 0 of 3 under General Editing) and an overclaiming
   paragraph gained a caveat the author never wrote.
2. A blanket ban on ", verb+ing" rewrote correct academic prose in every run
   (", indicating that" became ", which indicates that", 3 of 3) regardless of
   context, inside a contract that promises minimal changes.
3. The Document Context setting did nothing at all on two of the four paths:
   the polish prompt returned identical text for journal and general, and the
   live preview had no context parameter.
4. Smaller conflicts: the banned-word list included technical terms ("robust
   standard errors"); the citation rules both permitted and forbade moving a
   citation; the strict editing contract was stated twice per request; the
   em-dash advice produced comma splices; and the temperature slider was
   labelled as a style control while the Style combo picks the prompt and
   Creative silently forces temperature >= 0.5.

## What changed

- **Context placement**: both proofreading prompts now carry the context block
  above the scope rules, under a sentence that says it never widens the scope.
- **Context content**: the journal context is now explicitly evidence-neutral:
  no cutting of sentences, caveats or citations, no softening or strengthening
  of claims, technical terms left alone, structural observations left to a
  review comment. The general and thesis contexts carry the same precedence
  line.
- **Language rules**: ", verb+ing" is a preference with an explicit allowance
  for idiomatic academic use; "robust", "nuanced" and "underscores" are
  permitted as technical or plain-English uses; the em-dash replacement advice
  distinguishes an aside from two independent clauses.
- **Claim protection**: every prompt (precise, creative, four polish variants,
  two live-preview variants) now forbids changing claim strength, hedging,
  statistical significance, numbers or terminology. The preview prompts give
  the concrete examples ("causes" does not become "is associated with") and an
  escape hatch for when a claim's strength is the only thing that looks wrong.
- **One contract**: the prompt files point at the OUTPUT CONTRACT that the
  loader appends, instead of carrying a second copy of the same rules. The
  loader now looks for the contract's own text, not the words "OUTPUT
  CONTRACT", so pointing at it cannot silently suppress it.
- **Consistency**: `load_context_summary` gives each context a single line,
  which the polish and live-preview prompts now carry; `preview_edits_once`
  reads the setting. The comment path already used the full overlay.
- **UI copy**: the slider is now "Editing freedom (temperature)" between "More
  conservative" and "More rewriting", and both the tooltip and the Editing
  Style hint state that Creative (Rewrite) always uses at least 0.5.

## Deliberately left alone

- The live preview's 12-edit cap: raising it changes how much a single
  selection can return, which is a product decision rather than a defect.
- The polish path's automation rule (text from Mail or webmail switches to the
  Email context); the live preview still uses the setting without that
  override.
- Local-model behaviour: the probes ran against the configured cloud provider.
