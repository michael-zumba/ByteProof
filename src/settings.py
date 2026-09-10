import json
import os
import platform
import sys
from typing import Any

from config.deepseek_config import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_CHAT_MODEL,
    get_deepseek_api_keys,
)

from .automation import default_automation_rules

APP_NAME = "ByteProof"
APP_VERSION = "2.0.2-beta.7"
COMPANY_NAME = "ByteMind Ltd"
COMPANY_URL = "https://www.bytemind.co.nz"
PRODUCT_URL = "https://www.bytemind.co.nz/byteproof"
SUPPORT_EMAIL = "bytemind.nz@gmail.com"

# Polar licensing is the canonical payment + license-owner path (the VoiceInk
# approach). Prices and checkout links are managed in the Polar dashboard;
# set these env vars to override them for development.
POLAR_API_URL = "https://api.polar.sh"
POLAR_ORGANIZATION_ID = os.environ.get(
    "BYTEPROOF_POLAR_ORGANIZATION_ID", ""
).strip() or "710df3ef-fa69-4904-98f7-676fad519615"
POLAR_CHECKOUT_URL = os.environ.get(
    "BYTEPROOF_POLAR_CHECKOUT_URL", ""
).strip() or (
    "https://buy.polar.sh/polar_cl_m1VuSWJu14vqCyvzt13bLpTfKEV20qfRTdaNy1ApIIR"
)

# Developer-only identities that unlock full access without a Polar key.
#
# Shipped builds carry NONE: a published address must never be a master key.
# Access is granted only when the machine has an explicit local configuration,
# either an environment variable or a dev-access.json file in the support
# folder (see developer_emails()). Customers always use Polar keys.
DEVELOPER_EMAILS: tuple[str, ...] = ()
DEV_ACCESS_FILE = "dev-access.json"
DEV_EMAILS_ENV = "BYTEPROOF_DEV_EMAILS"

_dev_emails_cache: tuple[tuple[Any, ...], tuple[str, ...]] | None = None


def developer_emails() -> tuple[str, ...]:
    """Return locally-configured developer identities (usually none).

    Sources, in order: the ``BYTEPROOF_DEV_EMAILS`` environment variable and
    ``dev-access.json`` in the app support directory. The file is created by
    the owner with ``scripts/dev_access.py`` and is never shipped, so knowing
    an email address is not enough to unlock an installed build.

    The result is memoised against the environment value and the file's
    modification time so licence checks stay cheap.
    """
    global _dev_emails_cache

    env_value = os.environ.get(DEV_EMAILS_ENV, "")
    found: list[str] = []
    for value in env_value.split(","):
        text = value.strip().lower()
        if text and "@" in text:
            found.append(text)

    path = os.path.join(get_app_support_dir(), DEV_ACCESS_FILE)
    try:
        stamp: Any = os.stat(path).st_mtime_ns
    except OSError:
        stamp = None
    key = (env_value, stamp)
    if _dev_emails_cache is not None and _dev_emails_cache[0] == key:
        return tuple(dict.fromkeys(found + list(_dev_emails_cache[1])))

    file_emails: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        values = data.get("emails", []) if isinstance(data, dict) else data
        if isinstance(values, list):
            for value in values:
                text = str(value).strip().lower()
                if text and "@" in text:
                    file_emails.append(text)
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        file_emails = []

    _dev_emails_cache = (key, tuple(dict.fromkeys(file_emails)))
    return tuple(dict.fromkeys(found + file_emails))

LOCAL_MODEL_PROVIDER = "ByteProof Local (Qwen3)"


def get_app_support_dir() -> str:
    """Return the per-platform data directory for ByteProof."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ByteMind", "ByteProof")
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/ByteMind/ByteProof")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "ByteMind", "ByteProof")


APP_SUPPORT_DIR = get_app_support_dir()
SETTINGS_FILE = os.path.join(APP_SUPPORT_DIR, "settings.json")
LOCAL_MODEL_DIR = os.path.join(APP_SUPPORT_DIR, "local-models")
RUNTIME_DIR = os.path.join(APP_SUPPORT_DIR, "runtime")

# Define available providers
PROVIDERS = {
    LOCAL_MODEL_PROVIDER: {
        "base_url": "",
        "model": "phi4-mini",
        "default_keys": [],
        "is_free": True,
        "is_local": True,
        "badge": "LOCAL",
        # llama-server runs with a total --ctx-size of 8192 (prompt +
        # completion), so an 8192-token output cap could never fit. 2048 leaves
        # room for the prompt; a reply that still hits the cap is refused by
        # the truncation guard instead of being half-applied.
        "max_output_tokens": 2048,
        "install_guide": (
            "ByteProof downloads a small local model (Phi-4 Mini or Qwen3) to "
            "your computer and runs it privately — no API key, no account, "
            "and it stays available in the limited free mode after your "
            "7-day trial. The $49 license unlocks unlimited use.\n\n"
            "Open the Local AI tab to pick a model and download it. The "
            "recommended size depends on your RAM."
        ),
    },
    "Ollama (Local)": {
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.2:3b",
        "default_keys": [],
        "is_free": True,
        "max_output_tokens": 32768,
        "install_guide": (
            "Install Ollama from https://ollama.com, then run:\n"
            "  ollama pull llama3.2:3b\n\n"
            "For better results, try:\n"
            "  ollama pull llama3.2    (larger, 3B model)\n"
            "  ollama pull mistral     (7B model)\n"
            "  ollama pull gemma3:4b   (Google's Gemma)"
        ),
    },
    "DeepSeek": {
        "base_url": DEEPSEEK_BASE_URL,
        "model": DEEPSEEK_CHAT_MODEL,
        "default_keys": get_deepseek_api_keys(),
        "max_output_tokens": 192000,
    },
    "Google Gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.8-flash",
        "default_keys": [],
        "max_output_tokens": 65536,
        "install_guide": "Get a free API key at https://aistudio.google.com/apikey",
    },
    "Groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "default_keys": [],
        "max_output_tokens": 32768,
        "install_guide": "Get a free API key at https://console.groq.com/keys",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.5",
        "default_keys": [],
        "max_output_tokens": 32768,
    },
    "Anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-5",
        "default_keys": [],
        "max_output_tokens": 64000,
    },
    "xAI": {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.6",
        "default_keys": [],
        "max_output_tokens": 65536,
    },
    "Perplexity": {
        "base_url": "https://api.perplexity.ai",
        "model": "sonar-pro",
        "default_keys": [],
        "max_output_tokens": 32768,
    },
}

# Models ByteProof shipped as defaults in earlier versions. When a saved
# provider still points at one of these, it was never customised, so it is
# refreshed to the current default (a user's own model choice is preserved).
# Verified 2026-09-10: DeepSeek "deepseek-v4-flash" is retired (served by
# V4.1-Flash), Groq dropped "llama-3.1-70b-versatile", and Google/OpenAI/
# Anthropic/xAI have all superseded the listed snapshots.
SUPERSEDED_DEFAULT_MODELS: dict[str, frozenset[str]] = {
    "DeepSeek": frozenset(
        {"deepseek-v4-flash", "deepseek-v4-flash-vision-exp", "deepseek-chat", "deepseek-reasoner"}
    ),
    "Google Gemini": frozenset(
        {"gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-flash"}
    ),
    "Groq": frozenset(
        {
            "llama-3.1-70b-versatile",
            "llama-3.1-8b-instant",
            "llama-3.3-70b-versatile",
            "mixtral-8x7b-32768",
        }
    ),
    "OpenAI": frozenset({"gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4.1", "gpt-5", "gpt-5.1"}),
    "Anthropic": frozenset(
        {
            "claude-sonnet-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-opus-4-20250514",
        }
    ),
    "xAI": frozenset({"grok-3-beta", "grok-3", "grok-3-mini", "grok-4", "grok-4-5"}),
}


def refresh_superseded_models(settings: dict[str, Any]) -> list[str]:
    """Point never-customised providers at the current default model.

    Only models ByteProof itself used to ship are replaced, so a model the
    user chose deliberately is never touched. Returns the providers updated
    (used for logging and tests).
    """
    updated: list[str] = []
    providers = settings.get("providers")
    if not isinstance(providers, dict):
        return updated
    for name, superseded in SUPERSEDED_DEFAULT_MODELS.items():
        config = providers.get(name)
        if not isinstance(config, dict):
            continue
        current = str(config.get("model", "")).strip()
        if not current or current not in superseded:
            continue
        new_model = PROVIDERS.get(name, {}).get("model")
        if new_model and new_model != current:
            config["model"] = new_model
            updated.append(name)
    return updated

def resource_path(relative_path: str) -> str:
    """ Get absolute path to resource, works for dev and for PyInstaller """
    bundle_path = getattr(sys, "_MEIPASS", None)
    if bundle_path:
        return os.path.join(bundle_path, relative_path)
        
    if getattr(sys, 'frozen', False):
        # The application is frozen
        base_path = os.path.dirname(sys.executable)
        
        # Check if we are in a .app bundle (macOS)
        if 'Contents/MacOS' in base_path:
             resources_path = os.path.join(os.path.dirname(base_path), 'Resources')
             path = os.path.join(resources_path, relative_path)
             if os.path.exists(path):
                 return path
                 
        return os.path.join(base_path, relative_path)

    # Walk up from src/settings.py to project root
    # src/settings.py -> src -> root
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, relative_path)

def _normalise_base_url(base_url: str) -> str:
    cleaned = base_url.strip()
    cleaned = cleaned.removesuffix("/")
    return cleaned

def _clean_api_keys(keys: list[str]) -> list[str]:
    return [key.strip() for key in keys if isinstance(key, str) and key.strip()]

def load_runtime_settings() -> dict[str, Any]:
    # Default structure
    settings: dict[str, Any] = {
        "app_version": APP_VERSION,
        "general": {
            "launch_at_login": False,
            "keep_on_top": True,
            "auto_apply": True,
            "track_changes": True,
            "play_sound_on_proofread": True,
            "temperature": 0.3,
            "spelling": "UK/AU/NZ",
            "style": "Precise (Minimal Changes)",
            "comment_type": "None",
            "context": "General Editing",
            "open_hotkey": "<cmd>+<shift>+;",
            "proofread_hotkey": "<cmd>+<shift>+'",
        },
        "active_provider": LOCAL_MODEL_PROVIDER,
        "local_model": {
            "active_model": None,
            "auto_download": True,
        },
        "providers": {},
        "license": {
            "status": "unlicensed",
            "key": "",
            "email": "",
            "activated_at": 0,
            "expiry": 0,
        },
        "automation": {
            "enabled": True,
            "rules": default_automation_rules(),
        },
        "live_preview": {
            "enabled": True,
            "delay_ms": 600,
            "max_chars": 1500,
            "use_local_model": True,
            "style": "strict",
        },
    }
    
    # Initialize providers with defaults
    for name, config in PROVIDERS.items():
        base_url_val = config.get("base_url")
        if isinstance(base_url_val, str):
            base_url_val = _normalise_base_url(base_url_val)
        else:
            base_url_val = ""

        settings["providers"][name] = {
            "base_url": base_url_val,
            "model": config.get("model", ""),
            "api_keys": config.get("default_keys", []),
        }

    if not os.path.exists(SETTINGS_FILE):
        settings["general"]["temperature"] = 0.3
        settings["app_version"] = APP_VERSION
        return settings

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError):
        settings["general"]["temperature"] = 0.3
        settings["app_version"] = APP_VERSION
        return settings

    # Migration logic
    if "api_keys" in loaded and isinstance(loaded["api_keys"], list):
        # Old format migration
        cleaned_keys = _clean_api_keys(loaded.get("api_keys", []))
        if cleaned_keys:
            settings["providers"]["DeepSeek"]["api_keys"] = cleaned_keys
        
        if "base_url" in loaded:
            settings["providers"]["DeepSeek"]["base_url"] = _normalise_base_url(loaded["base_url"])
        if "model" in loaded:
            settings["providers"]["DeepSeek"]["model"] = loaded["model"].strip()
            
        settings["general"]["temperature"] = 0.3
    else:
        # New format
        if "general" in loaded:
            settings["general"].update(loaded["general"])
            if "temperature" not in loaded["general"]:
                settings["general"]["temperature"] = 0.3
        else:
             settings["general"]["temperature"] = 0.3
        
        if "active_provider" in loaded and loaded["active_provider"] in PROVIDERS:
            settings["active_provider"] = loaded["active_provider"]
            
        if "providers" in loaded:
            for name, data in loaded["providers"].items():
                if name in settings["providers"]:
                    if "base_url" in data:
                        settings["providers"][name]["base_url"] = _normalise_base_url(data["base_url"])
                    if "model" in data:
                        settings["providers"][name]["model"] = data["model"]
                    if "api_keys" in data:
                        settings["providers"][name]["api_keys"] = _clean_api_keys(data["api_keys"])
        
        if "license" in loaded:
            settings["license"].update(loaded["license"])

    if "local_model" in loaded:
        settings["local_model"].update(loaded["local_model"])

    if "automation" in loaded:
        settings["automation"].update(loaded["automation"])

    if "live_preview" in loaded:
        settings["live_preview"].update(loaded["live_preview"])

    _migrate_mac_hotkeys(settings)
    refreshed = refresh_superseded_models(settings)
    if refreshed:
        print(f"Updated default models for: {', '.join(refreshed)}")
    settings["general"]["temperature"] = max(0.0, min(2.0, settings["general"]["temperature"]))
    _stamp_version_and_save(settings, force=bool(refreshed))
    return settings


def note_launch_version(settings: dict[str, Any]) -> bool:
    """Record this launch's version; True when it follows an app update.

    Used to show the post-update Accessibility re-grant hint for live
    suggestions. Never mutates anything else in the settings dict.
    """
    current = str(settings.get("app_version") or "")
    previous = str(settings.get("last_run_version") or "")
    settings["last_run_version"] = current
    return bool(previous) and previous != current


def _migrate_mac_hotkeys(settings: dict[str, Any]) -> None:
    """Repair hotkeys corrupted by the old Cmd/Ctrl conversion on macOS.

    Older builds mapped Qt's "Ctrl" (which is the Command key on macOS) to the
    physical Control key when saving. Restore the shipped defaults when the
    stored value matches the exact corrupted form, so users are not silently
    left with a hotkey they never chose.
    """
    if platform.system() != "Darwin":
        return
    general = settings.setdefault("general", {})
    if general.get("proofread_hotkey") == "<ctrl>+<shift>+'":
        general["proofread_hotkey"] = "<cmd>+<shift>+'"
    if general.get("open_hotkey") == "<ctrl>+<shift>+;":
        general["open_hotkey"] = "<cmd>+<shift>+;"


def _stamp_version_and_save(settings: dict[str, Any], force: bool = False) -> None:
    """Record the app version that last wrote settings.json.

    This lets future releases detect an upgrade and run one-time migrations,
    while never touching the user's hotkeys, license, or other preferences.
    ``force`` persists the file even when the version is unchanged, which is
    needed when a migration changed something (e.g. refreshed model defaults).
    """
    stored_version = settings.get("app_version")
    settings["app_version"] = APP_VERSION
    if stored_version == APP_VERSION and not force:
        return
    try:
        # Reuse the atomic, 0600 writer: this file holds provider API keys.
        save_runtime_settings(settings)
    except OSError:
        pass

def save_runtime_settings(settings: dict[str, Any]) -> None:
    os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
    if "providers" not in settings:
        defaults = load_runtime_settings()
        for key in defaults:
            if key not in settings:
                settings[key] = defaults[key]
    settings.setdefault("local_model", {"active_model": None, "auto_download": True})

    # The file holds provider API keys, so keep it readable only by the user.
    tmp_path = SETTINGS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    os.replace(tmp_path, SETTINGS_FILE)
