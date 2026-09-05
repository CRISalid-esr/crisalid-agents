from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


def message_from_role(role: str | None, content: str) -> BaseMessage | None:
    if role == "system":
        return SystemMessage(content=content)
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        return AIMessage(content=content)
    return None


def to_langchain_messages(messages: list[dict]) -> list[BaseMessage]:
    # OpenAI-style {"role": ..., "content": ...} dicts (OpenWebUI format).
    result: list[BaseMessage] = []
    for message in messages:
        converted = message_from_role(message.get("role"), message.get("content", ""))
        if converted is not None:
            result.append(converted)
    return result
