"""
DeepSeek API Configuration

Centralized configuration for DeepSeek API keys and settings.
This file securely stores multiple API keys for random selection.
"""

import os
import random
import shutil

# DeepSeek API Configuration
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Model Configurations
DEEPSEEK_CHAT_MODEL = "deepseek-v4-flash"
DEEPSEEK_REASONER_MODEL = "deepseek-reasoner"

# Context and Output Limits
# Context length is shared (1M), but output limits differ
MAX_CONTEXT_LENGTH = 1000000
MAX_OUTPUT_CHAT = 384000
MAX_OUTPUT_REASONER = 384000

# Default safe margins (to leave room for input context)
DEFAULT_MAX_OUTPUT_CHAT = 192000
DEFAULT_MAX_OUTPUT_REASONER = 192000

# Helper to get limits by model name
MODEL_LIMITS = {
    DEEPSEEK_CHAT_MODEL: {
        "context": MAX_CONTEXT_LENGTH,
        "output": MAX_OUTPUT_CHAT
    },
    DEEPSEEK_REASONER_MODEL: {
        "context": MAX_CONTEXT_LENGTH,
        "output": MAX_OUTPUT_REASONER
    }
}

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
    valid_keys = [key.strip() for key in raw.split(",") if key.strip()]
    return valid_keys

def get_random_api_key() -> str:
    """
    Get a randomly selected API key from the available keys.
    
    Returns:
        str: Randomly selected API key, or empty string if no keys configured
    """
    api_keys = get_deepseek_api_keys()
    if not api_keys:
        return ""
    return random.choice(api_keys)

def validate_api_keys() -> bool:
    """
    Validate that API keys are properly configured.
    
    Returns:
        bool: True if keys are valid, False otherwise
    """
    keys = get_deepseek_api_keys()
    if not keys:
        print("No DeepSeek API keys configured. Set DEEPSEEK_API_KEYS env var.")
        return False
    print(f"Found {len(keys)} valid DeepSeek API keys")
    for i, key in enumerate(keys, 1):
        print(f"   Key {i}: {key[:8]}...{key[-8:]}")
    return True

def get_deepseek_client():
    """
    Get an initialized OpenAI client for DeepSeek API
    with a randomly selected API key.

    Returns:
        OpenAI client instance
    """
    try:
        from openai import OpenAI
        return OpenAI(api_key=get_random_api_key(), base_url=DEEPSEEK_BASE_URL)
    except ImportError:
        raise ImportError("The 'openai' package is required. Please install it using 'pip install openai'")


def get_random_client():
    """
    Alias for get_deepseek_client(). Creates a fresh client
    with a newly randomly selected API key on every call.
    Use this before each API request to distribute load across keys.
    """
    return get_deepseek_client()


def clean_pycache(root_dir=None):
    """
    Remove all __pycache__ directories and .pyc files
    from the project tree to avoid stale imports.

    Args:
        root_dir (str, optional): Root directory to clean.
            Defaults to the project root (two levels up from this file).
    """
    if root_dir is None:
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))

    removed_dirs = 0
    removed_files = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        if '__pycache__' in dirnames:
            cache_path = os.path.join(dirpath, '__pycache__')
            try:
                shutil.rmtree(cache_path)
                removed_dirs += 1
            except Exception:
                pass

        for filename in filenames:
            if filename.endswith('.pyc') or filename.endswith('.pyo'):
                file_path = os.path.join(dirpath, filename)
                try:
                    os.remove(file_path)
                    removed_files += 1
                except Exception:
                    pass

    if removed_dirs > 0 or removed_files > 0:
        print(f"🧹 Cache cleaned: {removed_dirs} __pycache__ dir(s), "
              f"{removed_files} .pyc/.pyo file(s) removed")

def chat_with_reasoner_multiround(messages, client=None, stream=True):
    """
    Helper function to perform a chat completion with the DeepSeek reasoner model,
    ensuring thinking mode is activated properly for multi-round conversations.
    
    Args:
        messages (List[dict]): The conversation history.
        client: The OpenAI client instance (optional).
        stream (bool): Whether to stream the response.
        
    Returns:
        If stream=True: returns the response generator.
        If stream=False: returns a tuple of (reasoning_content, final_content).
    """
    if client is None:
        client = get_deepseek_client()
        
    # In thinking mode, temperature, top_p, presence_penalty, frequency_penalty have no effect.
    response = client.chat.completions.create(
        model=DEEPSEEK_REASONER_MODEL,
        messages=messages,
        stream=stream,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}}
    )
    
    return response

def process_reasoner_stream(response):
    """
    Helper function to process the stream from chat_with_reasoner_multiround.
    
    Args:
        response: The streaming response from the API.
        
    Returns:
        tuple: (reasoning_content, content)
    """
    reasoning_content = ""
    content = ""
    
    for chunk in response:
        if hasattr(chunk.choices[0].delta, 'reasoning_content') and chunk.choices[0].delta.reasoning_content:
            reasoning_content += chunk.choices[0].delta.reasoning_content
        elif chunk.choices[0].delta.content:
            content += chunk.choices[0].delta.content
            
    return reasoning_content, content

def append_assistant_message(messages, reasoning_content, content, has_tool_call=False):
    """
    Append assistant message to context for multi-round conversation.
    
    Args:
        messages (List[dict]): The conversation history to append to.
        reasoning_content (str): The chain-of-thought content.
        content (str): The final answer.
        has_tool_call (bool): Whether a tool call was performed.
                              If True, reasoning_content MUST participate in context.
                              If False, reasoning_content should be omitted.
    """
    if has_tool_call:
        messages.append({
            "role": "assistant",
            "reasoning_content": reasoning_content,
            "content": content
        })
    else:
        messages.append({
            "role": "assistant",
            "content": content
        })
    return messages

if __name__ == "__main__":
    # Test the configuration
    print("🧪 Testing DeepSeek API Configuration...")
    if validate_api_keys():
        print("✅ Configuration test passed!")
        print(f"🔑 Random key selected: {get_random_api_key()[:8]}...{get_random_api_key()[-8:]}")
    else:
        print("❌ Configuration test failed!")