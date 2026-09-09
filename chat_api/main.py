"""FastAPI chat API exposing every registered agent to MUI X Chat frontends.

- ``GET  /health``               unauthenticated health check
- ``GET  /agents``               list of served agents (name, display name, description)
- ``POST /agents/{name}/chat``   NDJSON stream of MUI X Chat message chunks

Run with:
    uv run uvicorn chat_api.main:app --port 9100 --reload
"""

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from chat_api.auth import require_api_key  # noqa: E402
from chat_api.streaming import ndjson_chunks  # noqa: E402
from common.agent import BaseAgent  # noqa: E402
from common.messages import message_from_role  # noqa: E402
from common.registry import UnknownAgentError, registry  # noqa: E402


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
        converted = message_from_role(message.get("role"), content)
        if converted is not None:
            result.append(converted)
    return result


def resolve_agent(name: str) -> BaseAgent:
    try:
        return registry.get_agent(name)
    except UnknownAgentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await registry.aclose_all()


# No CORS middleware: the chat API is only called server-to-server from the
# internal Docker network (sovisuplus backend), never directly from a browser.
app = FastAPI(title="CRISalid agents chat API", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/agents", dependencies=[Depends(require_api_key)])
async def list_agents() -> list[dict]:
    return registry.describe_agents()


@app.post("/agents/{name}/chat", dependencies=[Depends(require_api_key)])
async def chat(name: str, request: ChatRequest) -> StreamingResponse:
    agent = resolve_agent(name)
    messages = to_langchain_messages(request)
    return StreamingResponse(ndjson_chunks(agent, messages), media_type="application/x-ndjson")
