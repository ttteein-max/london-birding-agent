"""Environment-driven model factory for the standalone biodiversity agent."""

import os

from langchain_openai import ChatOpenAI


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required for live mode")
    return value


def create_live_chat_model() -> ChatOpenAI:
    """Create a configurable model without embedding credentials or model IDs."""

    kwargs = {
        "model": _required_env("OPENAI_MODEL"),
        "api_key": _required_env("OPENAI_API_KEY"),
        "timeout": 60.0,
        "max_retries": 2,
    }
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)
