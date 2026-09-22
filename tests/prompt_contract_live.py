"""Live check that the proofreading prompts keep their promises.

Run manually with a configured provider (it makes a handful of small calls):

    ./venv/bin/python tests/prompt_contract_live.py

The prompts are the product here, so the invariants need to be checkable
against the real model rather than only against the text of the prompt files.
Each case below is one that used to fail:

* the top-tier journal context rewrote a fine word ("important" -> "matter")
  and answered an overclaiming paragraph with a caveat the author never wrote;
* the blanket ban on ", verb+ing" rewrote ", indicating that" every time,
  inside a minimal-change contract;
* throat-clearing and an em dash still have to go, and numbers have to stay.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import logic
from src.settings import load_runtime_settings

JOURNAL = "Academic Journal (Top-Tier)"

STATISTICS = (
    "Our regression results show that the treatment effect is statistically "
    "significant at the 1% level and economically large. The coefficient on "
    "board independence is 0.042 (t = 3.18), indicating that a "
    "one-standard-deviation increase in board independence is associated with a "
    "4.2% increase in Tobin's Q. These findings are important because they "
    "suggest that governance reforms improve firm value."
)

OVERCLAIMING = (
    "The results demonstrate that board independence causes higher firm value. "
    "This finding proves that governance reforms are effective and that all "
    "firms should adopt independent boards."
)

THROAT_CLEARING = (
    "It is important to note that the sample \u2014 which was collected over ten "
    "years \u2014 contains 2,341 firms. Interestingly, the results were robust, "
    "and this suggests that the effect is real."
)


def main() -> int:
    settings = load_runtime_settings()
    provider, api_key, base_url, model = logic.resolve_provider_connection(settings)
    if not api_key:
        print(f"No API key configured for {provider}; nothing to check.")
        return 0

    failures: list[str] = []

    def proofread(text: str, context: str = JOURNAL) -> str:
        return logic.proofread_with_provider(
            text,
            api_key,
            max_tokens=1500,
            base_url=base_url,
            model=model,
            provider_name=provider,
            temperature=0.3,
            spelling="UK/AU/NZ",
            style="Precise (Minimal Changes)",
            context=context,
        )

    print("[statistics paragraph: the author's words and numbers stay put]")
    out = proofread(STATISTICS)
    if "important" not in out:
        failures.append("'important' was rewritten")
    if ", indicating that" not in out:
        failures.append("', indicating that' was rewritten")
    for token in ("1%", "0.042", "3.18", "4.2%", "Tobin's Q"):
        if token not in out:
            failures.append(f"{token} went missing")
    print(f"  {'ok' if not failures else 'FAILED'}: {out[:90]}...")

    print("[overclaiming paragraph: no caveat is invented, no claim is softened]")
    out = proofread(OVERCLAIMING)
    if "although" in out.lower():
        failures.append("a caveat the author did not write was added")
    if "causes" not in out:
        failures.append("the causal claim was softened")
    if "proves" not in out and "should adopt" not in out:
        failures.append("the author's recommendation was softened")
    print(f"  {'ok' if not failures else 'FAILED'}: {out[:90]}...")

    print("[throat-clearing and em dash: still edited, numbers still safe]")
    out = proofread(THROAT_CLEARING)
    if "It is important to note" in out:
        failures.append("throat-clearing survived")
    if "\u2014" in out:
        failures.append("the em dash survived")
    if "2,341" not in out:
        failures.append("the sample size was altered")
    print(f"  {'ok' if not failures else 'FAILED'}: {out[:90]}...")

    if failures:
        print("\nPROMPT_CONTRACT_FAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nPROMPT_CONTRACT_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
