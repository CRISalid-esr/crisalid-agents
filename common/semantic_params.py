"""Helpers for the ``semantic_*`` parameter convention of the CRISalid MCP Toolbox.

A ``semantic_xxx`` tool parameter carries a natural-language string supplied by the LLM.
Before the tool runs, ``embed_semantic_params`` embeds that string and passes the vector in
the paired ``semantic_xxx_vector`` parameter (both must be declared by the tool, see
``MCPToolboxClient._validate_semantic_params``). ``strip_vector_args`` removes those vectors
from the conversation history before it is replayed to the LLM.

Agents opt in by piping their LLM through ``RunnableLambda(embed_semantic_params)`` and
wrapping the messages they send with ``strip_vector_args``.
"""

from langchain_core.messages import AIMessage, BaseMessage

from common.embedding import EmbeddingServiceError, get_embedding_provider

VECTOR_SUFFIX = "_vector"
SEMANTIC_PREFIX = "semantic_"


def _is_semantic_text(key: str, value) -> bool:
    return key.startswith(SEMANTIC_PREFIX) and not key.endswith(VECTOR_SUFFIX) and isinstance(value, str)


def strip_vector_args(messages: list[BaseMessage]) -> list[BaseMessage]:
    result = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            cleaned = [
                {**tc, "args": {k: v for k, v in tc["args"].items() if not k.endswith(VECTOR_SUFFIX)}}
                for tc in msg.tool_calls
            ]
            result.append(AIMessage(content=msg.content, tool_calls=cleaned))
        else:
            result.append(msg)
    return result


async def embed_semantic_params(message: AIMessage) -> AIMessage:
    if not message.tool_calls:
        return message

    needs_embedding = any(
        any(_is_semantic_text(k, v) for k, v in tc["args"].items())
        for tc in message.tool_calls
    )
    if not needs_embedding:
        return message

    try:
        provider = get_embedding_provider()
        new_tool_calls = []
        for tc in message.tool_calls:
            new_args = dict(tc["args"])
            for key, value in list(tc["args"].items()):
                if _is_semantic_text(key, value):
                    new_args[f"{key}{VECTOR_SUFFIX}"] = await provider.embed_text(value)
            new_tool_calls.append({**tc, "args": new_args})
        return AIMessage(content=message.content, tool_calls=new_tool_calls)
    except EmbeddingServiceError as exc:
        return AIMessage(
            content=f"Error: the embedding service is currently unavailable ({exc}). Please try again later.",
            tool_calls=[],
        )
