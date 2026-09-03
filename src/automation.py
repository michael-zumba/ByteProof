"""Automatic context selection for generic text editing.

ByteProof edits text in many applications. Some of those applications are
email clients or browser-based webmail. This module turns a frontmost-app
record (and, when available, the active browser URL) into a named editing
context, such as ``Email Editing``.

Rules are deliberately small and explicit. A rule value can be prefixed with
``bundle:``, ``url:``, ``name:``, or ``exe:``. Plain values are inferred so a
user can type either an app name, a bundle id, or a domain without needing to
know the prefix.
"""

from __future__ import annotations

from typing import Any

EMAIL_CONTEXT = "Email Editing"

DEFAULT_AUTOMATION_RULES: list[dict[str, str]] = [
    {"source": "bundle:com.apple.mail", "context": EMAIL_CONTEXT},
    {"source": "bundle:com.microsoft.outlook", "context": EMAIL_CONTEXT},
    {"source": "bundle:com.microsoft.Outlook", "context": EMAIL_CONTEXT},
    {"source": "exe:OUTLOOK.EXE", "context": EMAIL_CONTEXT},
    {"source": "name:Microsoft Outlook", "context": EMAIL_CONTEXT},
    {"source": "name:Gmail", "context": EMAIL_CONTEXT},
    {"source": "url:mail.google.com", "context": EMAIL_CONTEXT},
    {"source": "url:outlook.live.com", "context": EMAIL_CONTEXT},
    {"source": "url:outlook.office.com", "context": EMAIL_CONTEXT},
    {"source": "url:mail.yahoo.com", "context": EMAIL_CONTEXT},
]


def default_automation_rules() -> list[dict[str, str]]:
    """Return a fresh copy of the built-in trigger rules."""
    return [dict(rule) for rule in DEFAULT_AUTOMATION_RULES]


def _browser_url(target: dict[str, Any]) -> str:
    """Return the active browser URL when the target is a supported browser."""
    try:
        from .generic_editing import get_generic_editor

        return get_generic_editor().browser_url(target)
    except Exception:
        return ""


def source_display_label(source: str) -> str:
    """Return the human-readable value for a rule source, without its type."""
    value = source.strip()
    if not value:
        return ""
    if ":" in value:
        kind, _, rest = value.partition(":")
        if kind in ("bundle", "url", "name", "exe"):
            return rest
    return value


def source_type_label(source: str) -> str:
    """Return a short category label for a rule source."""
    value = source.strip()
    if ":" in value:
        kind = value.partition(":")[0]
        return {
            "bundle": "macOS app",
            "url": "Website",
            "name": "App name",
            "exe": "Windows app",
        }.get(kind, "Other")
    lower = value.lower()
    if (
        "://" in lower
        or lower.startswith("www.")
        or (lower.startswith("mail.") and " " not in lower)
        or (lower.count(".") >= 2 and " " not in lower and "/" not in lower)
    ):
        return "Website"
    if lower.startswith("com.") or lower.endswith(".app"):
        return "macOS app"
    return "App name"


def source_matches(
    target: dict[str, Any],
    source: str,
    browser_url: str = "",
) -> bool:
    """Return whether a frontmost app record matches one rule source."""
    value = source.strip()
    if not value:
        return False

    lower = value.lower()
    bundle = str(target.get("bundle_id", "")).lower()
    name = str(target.get("name", "")).lower()
    exe = str(target.get("exe", "")).lower()

    if ":" in value:
        kind, _, match = lower.partition(":")
        if kind in ("bundle", "url", "name", "exe") and match:
            if kind == "bundle":
                return bundle == match
            if kind == "url":
                return match in browser_url.lower()
            if kind == "name":
                return match in name
            if kind == "exe":
                return match in exe
        # Unknown prefixes fall through to plain inference so user text like
        # "http://..." is still handled reasonably.

    if (
        "://" in lower
        or lower.startswith("www.")
        or (lower.startswith("mail.") and " " not in lower)
        or (lower.count(".") >= 2 and " " not in lower and "/" not in lower)
    ):
        return lower in browser_url.lower()

    if lower.startswith("com.") or lower.endswith(".app"):
        return bundle == lower

    return lower in name or lower in exe


def _rules_from_settings(settings: dict[str, Any]) -> list[dict[str, str]]:
    """Return persisted rules or the defaults when no valid rules exist."""
    rules = (settings.get("automation") or {}).get("rules")
    if not isinstance(rules, list) or not rules:
        return default_automation_rules()

    cleaned: list[dict[str, str]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        source = str(rule.get("source") or rule.get("app") or "").strip()
        if not source:
            continue
        context = str(rule.get("context") or EMAIL_CONTEXT).strip()
        if context:
            cleaned.append({"source": source, "context": context})
    return cleaned or default_automation_rules()


def resolve_automation_context(
    target: dict[str, Any] | None,
    settings: dict[str, Any] | None,
) -> str | None:
    """Resolve the automatic editing context for a frontmost app."""
    if not target or not settings:
        return None

    automation = settings.get("automation") or {}
    if automation.get("enabled", True) is False:
        return None

    rules = _rules_from_settings(settings)
    if not rules:
        return None

    browser_url = ""
    for rule in rules:
        source = str(rule.get("source") or rule.get("app") or "").strip()
        if not source:
            continue
        if source.lower().startswith("url:"):
            if not browser_url:
                browser_url = _browser_url(target)
        elif source.lower().startswith(("bundle:", "name:", "exe:")):
            pass
        else:
            # Plain domain-like values also need the active browser URL.
            inferred = source.lower()
            if (
                "://" in inferred
                or inferred.startswith("www.")
                or (inferred.startswith("mail.") and " " not in inferred)
                or (
                    inferred.count(".") >= 2
                    and " " not in inferred
                    and "/" not in inferred
                )
            ) and not browser_url:
                browser_url = _browser_url(target)

        if source_matches(target, source, browser_url):
            context = str(rule.get("context") or EMAIL_CONTEXT).strip()
            return context or None
    return None
