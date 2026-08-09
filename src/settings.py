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

APP_NAME = "ByteProof"
APP_VERSION = "1.5.0"
COMPANY_NAME = "ByteMind Ltd"
COMPANY_URL = "https://www.bytemind.co.nz"
PRODUCT_URL = "https://www.bytemind.co.nz/byteproof"
SUPPORT_EMAIL = "bytemind.nz@gmail.com"

STRIPE_PAYMENT_URL = "https://buy.stripe.com/3cIcN50KZfX1bP3dN73Nm05"

# Polar licensing (the VoiceInk approach). Fill POLAR_ORGANIZATION_ID from
# Polar -> Settings -> Organization -> ID, then replace STRIPE_PAYMENT_URL with
# your Polar checkout link (Polar -> Products -> Checkout Links -> New Link).
POLAR_API_URL = "https://api.polar.sh"
POLAR_ORGANIZATION_ID = os.environ.get(
    "BYTEPROOF_POLAR_ORGANIZATION_ID", ""
).strip() or "710df3ef-fa69-4904-98f7-676fad519615"
POLAR_CHECKOUT_URL = os.environ.get(
    "BYTEPROOF_POLAR_CHECKOUT_URL", ""
).strip() or (
    "https://buy.polar.sh/polar_cl_m1VuSWJu14vqCyvzt13bLpTfKEV20qfRTdaNy1ApIIR"
)

# Developer-only email addresses that unlock full access without a Polar key.
# These are for the app owner / beta testers; customers always use Polar keys.
DEVELOPER_EMAILS: tuple[str, ...] = (
    "bytemind.nz@gmail.com",
)

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
        "max_output_tokens": 8192,
        "install_guide": (
            "ByteProof downloads a small local model (Phi-4 Mini or Qwen3) to "
            "your computer and runs it privately — no API key, no account, "
            "and it stays available in the limited free mode after your "
            "7-day trial. The $35 license unlocks unlimited use.\n\n"
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
        "model": "gemini-2.5-flash",
        "default_keys": [],
        "max_output_tokens": 65536,
        "install_guide": "Get a free API key at https://aistudio.google.com/apikey",
    },
    "Groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.1-70b-versatile",
        "default_keys": [],
        "max_output_tokens": 32768,
        "install_guide": "Get a free API key at https://console.groq.com/keys",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "default_keys": [],
        "max_output_tokens": 32768,
    },
    "Anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-20250514",
        "default_keys": [],
        "max_output_tokens": 64000,
    },
    "xAI": {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-3-beta",
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
        }
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

    _migrate_mac_hotkeys(settings)
    settings["general"]["temperature"] = max(0.0, min(2.0, settings["general"]["temperature"]))
    _stamp_version_and_save(settings)
    return settings


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


def _stamp_version_and_save(settings: dict[str, Any]) -> None:
    """Record the app version that last wrote settings.json.

    This lets future releases detect an upgrade and run one-time migrations,
    while never touching the user's hotkeys, license, or other preferences.
    """
    stored_version = settings.get("app_version")
    settings["app_version"] = APP_VERSION
    if stored_version == APP_VERSION:
        return
    try:
        os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
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
        
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
