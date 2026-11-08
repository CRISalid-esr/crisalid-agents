"""OpenAI-compatible routes of the chat API, for OpenWebUI.

- ``GET  /v1/models``            every served agent as a model (``id`` = agent name)
- ``POST /v1/chat/completions``  ``model`` = agent name; ``stream: true`` → SSE ``chat.completion.chunk`` lines
                                 ending with ``data: [DONE]``; otherwise one ``chat.completion`` object

OpenWebUI connects with the auth type *System OAuth*, so every request carries the signed-in person's
access token. Tool executions are rendered inside the assistant content as ``<details>`` blocks
(``common.tool_rendering``), with OpenWebUI status events keeping the spinner on while the model works
on a tool result.
"""

import json
import time
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict

from chat_api.auth import optional_user, require_user
from chat_api.deps import log_turn, resolve_agent, toolbox_token_for, visible_agents
from common.agent import BaseAgent, ToolCall, ToolResult
from common.messages import message_from_role
from common.tool_rendering import status_event, tool_result_block
from common.user_context import UserPrincipal, bind_user, stream_as_user

router = APIRouter(prefix="/v1")

OWNER = "crisalid-agents"


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict]
    stream: bool = False


def _content_text(content) -> str:
    # OpenAI content is a string, or a list of parts when the client attaches images.
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def to_langchain_messages(messages: list[dict]) -> list[BaseMessage]:
    result: list[BaseMessage] = []
    for message in messages:
        content = _content_text(message.get("content")).strip()
        if not content:
            continue
        converted = message_from_role(message.get("role"), content)
        if converted is not None:
            result.append(converted)
    return result


def _model_entry(agent: dict) -> dict:
    return {
        "id": agent["name"],
        "object": "model",
        "created": 0,
        "owned_by": OWNER,
        "name": agent["display_name"],
        "description": agent["description"],
    }


@router.get("/models")
async def list_models(principal: UserPrincipal | None = Depends(optional_user)) -> dict:
    return {"object": "list", "data": [_model_entry(agent) for agent in visible_agents(principal)]}


def _chunk(completion_id: str, created: int, model: str, delta: dict, finish_reason: str | None = None) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _sse(payload: dict | str) -> str:
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n"


async def sse_chunks(agent: BaseAgent, messages: list[BaseMessage]) -> AsyncGenerator[str, None]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    model = agent.name
    yield _sse(_chunk(completion_id, created, model, {"role": "assistant", "content": ""}))

    # OpenWebUI stops its waiting animation as soon as content arrives; after a tool result the model is
    # still working, so a status event re-opens the spinner until the next content token.
    pending_status = False
    async for item in agent.astream(messages):
        if isinstance(item, str):
            if pending_status:
                yield _sse(status_event(done=True))
                pending_status = False
            yield _sse(_chunk(completion_id, created, model, {"content": item}))
        elif isinstance(item, ToolCall):
            yield _sse(status_event(done=False, description=f"{item.name}…"))
            pending_status = True
        elif isinstance(item, ToolResult):
            # "\n\n" prefix avoids https://github.com/open-webui/open-webui/issues/24634
            yield _sse(_chunk(completion_id, created, model, {"content": "\n\n" + tool_result_block(item)}))
            yield _sse(status_event(done=False))
            pending_status = True
    if pending_status:
        yield _sse(status_event(done=True))
    yield _sse(_chunk(completion_id, created, model, {}, finish_reason="stop"))
    yield _sse("[DONE]")


async def collect_completion(agent: BaseAgent, messages: list[BaseMessage]) -> dict:
    parts: list[str] = []
    async for item in agent.astream(messages):
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, ToolResult):
            parts.append("\n\n" + tool_result_block(item))
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": agent.name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "".join(parts)},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@router.post("/chat/completions")
async def chat_completions(request: ChatCompletionRequest, principal: UserPrincipal = Depends(require_user)):
    agent = resolve_agent(request.model, principal)
    toolbox_token = await toolbox_token_for(principal)
    messages = to_langchain_messages(request.messages)
    log_turn(principal, agent.name, "openai")
    if request.stream:
        stream = stream_as_user(principal, toolbox_token, sse_chunks(agent, messages))
        return StreamingResponse(stream, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    with bind_user(principal, toolbox_token):
        return await collect_completion(agent, messages)
