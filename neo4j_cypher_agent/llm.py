import os

from langchain_openai import ChatOpenAI


def build_chat_model() -> ChatOpenAI:
    provider = os.getenv("LLM_PROVIDER", "ilaas").lower()

    if provider == "ilaas":
        return ChatOpenAI(
            model=os.environ["ILAAS_API_MODEL"],
            api_key=os.environ["ILAAS_API_KEY"],
            base_url=os.environ["ILAAS_API_URL"],
            temperature=0,
        )

    if provider == "openai":
        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.environ["OPENAI_API_KEY"],
            temperature=0,
        )

    raise ValueError(
        f"Unsupported LLM_PROVIDER={provider!r}. "
        "Expected 'ilaas' or 'openai'."
    )