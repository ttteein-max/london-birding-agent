"""Environment-driven production model factory for OpenAI-compatible APIs."""

import os

from langchain_openai import ChatOpenAI


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required for live mode")
    return value


def create_live_chat_model() -> ChatOpenAI:
    """Create a configurable model without embedding credentials or model IDs."""

    api_key = _required_env("OPENAI_API_KEY")
    model = _required_env("OPENAI_MODEL")
    base_url = os.getenv("OPENAI_BASE_URL")

    kwargs = {
        "model": model,
        "api_key": api_key,
        "timeout": 60.0,
        "max_retries": 2,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def create_live_models() -> tuple[ChatOpenAI, ChatOpenAI]:
    """Create separate investigator and finalizer model instances."""

    return create_live_chat_model(), create_live_chat_model()
