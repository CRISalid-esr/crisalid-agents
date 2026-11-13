import os

from langchain_openai import ChatOpenAI


def build_chat_model(model: str | None = None) -> ChatOpenAI:
    # ``model`` overrides the MODEL env var (e.g. a batch job with its own dedicated model).
    return ChatOpenAI(
        model=model or os.environ["MODEL"],
        api_key=os.environ["API_KEY"],
        base_url=os.environ.get("LLM_API_BASE"),
        temperature=0,
    )
