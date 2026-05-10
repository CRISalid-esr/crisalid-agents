import os

from langchain_openai import ChatOpenAI


def build_chat_model() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.environ["MODEL"],
        api_key=os.environ["API_KEY"],
        base_url=os.environ.get("LLM_API_BASE"),
        temperature=0,
    )
