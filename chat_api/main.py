"""FastAPI endpoint exposing CrisalidGraphAgent to MUI X Chat frontends.

Streams NDJSON: one JSON-encoded MUI X Chat message chunk per line
(start / text-start / text-delta / text-end / tool-input-available /
tool-output-available / finish). The frontend adapter pipes these lines
straight into the ChatBox runtime.

Run with:
    uv run uvicorn chat_api.main:app --port 9100 --reload
"""

import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from chat_api.auth import require_api_key  # noqa: E402
from crisalid_graph_agent.crisalid_graph_agent import CrisalidGraphAgent  # noqa: E402


class ChatRequest(BaseModel):
    conversationId: str | None = None
    message: dict
    messages: list[dict] = Field(default_factory=list)


def _text_of(message: dict) -> str:
    return "".join(
        part.get("text", "")
        for part in message.get("parts", [])
        if part.get("type") == "text"
    ).strip()


def to_langchain_messages(request: ChatRequest) -> list[BaseMessage]:
    thread = list(request.messages)
    if not thread or thread[-1].get("id") != request.message.get("id"):
        thread.append(request.message)

    result: list[BaseMessage] = []
    for message in thread:
        content = _text_of(message)
        if not content:
            continue
        role = message.get("role")
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
    return result


def _line(chunk: dict) -> str:
    return json.dumps(chunk, ensure_ascii=False) + "\n"


async def _stream_chunks(
    agent: CrisalidGraphAgent, messages: list[BaseMessage]
) -> AsyncGenerator[str, None]:
    message_id = f"msg-{uuid.uuid4()}"
    text_id: str | None = None
    text_block_count = 0

    yield _line({"type": "start", "messageId": message_id})
    try:
        async for item in agent.astream(messages):
            if isinstance(item, str):
                if text_id is None:
                    text_block_count += 1
                    text_id = f"{message_id}-text-{text_block_count}"
                    yield _line({"type": "text-start", "id": text_id})
                yield _line({"type": "text-delta", "id": text_id, "delta": item})

            elif isinstance(item, dict) and item.get("type") == "tool_result":
                # Tool activity interrupts any open text block: close it so the
                # tool invocation renders as its own message part.
                if text_id is not None:
                    yield _line({"type": "text-end", "id": text_id})
                    text_id = None
                yield _line(
                    {
                        "type": "tool-input-available",
                        "toolCallId": item["id"],
                        "toolName": item["name"],
                        "input": item["args"],
                    }
                )
                yield _line(
                    {
                        "type": "tool-output-available",
                        "toolCallId": item["id"],
                        "output": item["result"],
                    }
                )

        if text_id is not None:
            yield _line({"type": "text-end", "id": text_id})
        yield _line({"type": "finish", "messageId": message_id})

    except Exception as exc:  # noqa: BLE001 — surface any agent failure in the chat
        if text_id is not None:
            yield _line({"type": "text-end", "id": text_id})
        error_id = f"{message_id}-error"
        yield _line({"type": "text-start", "id": error_id})
        yield _line(
            {"type": "text-delta", "id": error_id, "delta": f"⚠️ Agent error: {exc}"}
        )
        yield _line({"type": "text-end", "id": error_id})
        yield _line(
            {"type": "finish", "messageId": message_id, "finishReason": "error"}
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent = CrisalidGraphAgent()
    try:
        yield
    finally:
        await app.state.agent.aclose()


# No CORS middleware: the chat API is only called server-to-server from the
# internal Docker network, never directly from a browser.
app = FastAPI(title="CRISalid chat API", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", dependencies=[Depends(require_api_key)])
async def chat(request: ChatRequest) -> StreamingResponse:
    messages = to_langchain_messages(request)
    return StreamingResponse(
        _stream_chunks(app.state.agent, messages),
        media_type="application/x-ndjson",
    )
