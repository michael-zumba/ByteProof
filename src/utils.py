import random


def clean_api_keys(keys: list[str]) -> list[str]:
    return [key.strip() for key in keys if isinstance(key, str) and key.strip()]

def mask_api_key(api_key: str) -> str:
    if len(api_key) <= 4:
        return api_key
    return f"...{api_key[-4:]}"

def select_api_key(api_keys: list[str], previous_key: str | None = None) -> str:
    cleaned_keys = clean_api_keys(api_keys)
    if not cleaned_keys:
        return "" # Return empty if no keys
    if previous_key and len(cleaned_keys) > 1:
        options = [key for key in cleaned_keys if key != previous_key]
        if options:
            return random.choice(options)
    return random.choice(cleaned_keys)
