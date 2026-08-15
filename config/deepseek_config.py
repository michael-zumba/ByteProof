"""
DeepSeek API configuration.

The app talks to DeepSeek over HTTP directly (OpenAI-compatible endpoint), so
this module only provides the base URL, the default model, and the API keys
read from the DEEPSEEK_API_KEYS environment variable (comma-separated).
"""

import os

# DeepSeek API Configuration
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Model Configurations
DEEPSEEK_CHAT_MODEL = "deepseek-v4-flash"

# Default safe margin for output tokens (leaves room for input context).
DEFAULT_MAX_OUTPUT_CHAT = 192000


def get_deepseek_api_keys() -> list[str]:
    """
    Get DeepSeek API keys from the DEEPSEEK_API_KEYS environment variable.
    Set the env var as a comma-separated list of keys.
    Example: export DEEPSEEK_API_KEYS="sk-xxx,sk-yyy,sk-zzz"

    Returns:
        List[str]: List of valid API keys (empty if not configured)
    """
    raw = os.environ.get("DEEPSEEK_API_KEYS", "")
    if not raw.strip():
        return []
    return [key.strip() for key in raw.split(",") if key.strip()]
