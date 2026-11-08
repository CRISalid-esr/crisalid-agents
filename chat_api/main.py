"""FastAPI chat API exposing every registered agent.

- ``GET  /health``               liveness probe, no authentication
- ``GET  /agents``               served agents (name, display name, description); filtered by the allow list
                                 when a token is sent
- ``POST /agents/{name}/chat``   MUI X Chat body, NDJSON stream of message chunks (SoVisu+)
- ``GET  /v1/models``, ``POST /v1/chat/completions``   OpenAI-compatible routes (OpenWebUI), see
                                 ``chat_api.openai_compat``

Chat routes require the signed-in person's Keycloak access token (``chat_api.auth``), check the agent
against the client's allow list (``chat_api.allow_list``) and exchange the token for the MCP toolbox
(``common.token_exchange``) before running the agent with the identity bound (``common.user_context``).

Run with:
    uv run uvicorn chat_api.main:app --port 9100 --reload
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Application logs (token rejections, allow-list decisions, chat turns, agent failures) go to stderr next to the
# uvicorn access log; LOG_LEVEL tunes their verbosity.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(levelname)s:     %(name)s: %(message)s",
)

from chat_api.allow_list import get_allow_list  # noqa: E402
from chat_api.auth import ApiError, get_verifier, optional_user, require_user  # noqa: E402
from chat_api.deps import get_exchanger, log_turn, resolve_agent, toolbox_token_for, visible_agents  # noqa: E402
from chat_api.openai_compat import router as openai_router  # noqa: E402
from chat_api.streaming import ndjson_chunks  # noqa: E402
from common.messages import message_from_role  # noqa: E402
from common.registry import registry  # noqa: E402
from common.user_context import UserPrincipal, stream_as_user  # noqa: E402


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail early on a missing KEYCLOAK_ISSUER and log the state of the optional pieces.
    get_verifier()
    get_allow_list()
    get_exchanger()
    try:
        yield
    finally:
        await registry.aclose_all()


# No CORS middleware: the chat API is called server-to-server (SoVisu+ backend, OpenWebUI), never from a browser.
app = FastAPI(title="CRISalid agents chat API", lifespan=lifespan)
app.include_router(openai_router)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.code}, headers=exc.headers)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/agents")
async def list_agents(principal: UserPrincipal | None = Depends(optional_user)) -> list[dict]:
    return visible_agents(principal)


@app.post("/agents/{name}/chat")
async def chat(name: str, request: ChatRequest, principal: UserPrincipal = Depends(require_user)) -> StreamingResponse:
    agent = resolve_agent(name, principal)
    toolbox_token = await toolbox_token_for(principal)
    messages = to_langchain_messages(request)
    log_turn(principal, agent.name, "agents")
    stream = stream_as_user(principal, toolbox_token, ndjson_chunks(agent, messages))
    return StreamingResponse(stream, media_type="application/x-ndjson")
