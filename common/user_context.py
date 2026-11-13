"""Identity of the person behind the current request, available to tool calls.

The chat API verifies the caller's access token, exchanges it for a toolbox token and binds both to the
request with ``bind_user``. Agents and their toolbox tools are created once per process, so they cannot
receive the identity as an argument: the MCP toolbox client reads ``current_toolbox_token`` when it builds
the headers of each tool call, and ``current_user`` is available to any code that needs to know who asks.

Both are ``contextvars.ContextVar``: a value bound in a task is visible to the tasks it spawns (LangGraph
nodes, ``ToolNode`` tool calls) and invisible to concurrent requests.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class UserPrincipal:
    sub: str
    username: str | None
    email: str | None
    client_id: str | None  # ``azp`` claim: the client application the token was issued to
    token: str  # the verified access token, as received
    exp: int  # ``exp`` claim, seconds since the epoch
    jti: str | None = None


current_user: ContextVar[UserPrincipal | None] = ContextVar("current_user", default=None)
current_toolbox_token: ContextVar[str | None] = ContextVar("current_toolbox_token", default=None)


@contextmanager
def bind_user(principal: UserPrincipal | None, toolbox_token: str | None) -> Iterator[None]:
    """Bind the identity to the current context for the duration of the block."""
    user_token = current_user.set(principal)
    toolbox = current_toolbox_token.set(toolbox_token)
    try:
        yield
    finally:
        current_toolbox_token.reset(toolbox)
        current_user.reset(user_token)


async def stream_as_user(principal: UserPrincipal | None, toolbox_token: str | None, stream: AsyncIterator):
    """Iterate ``stream`` with the identity bound.

    A streaming HTTP response is consumed after the request handler has returned, in the task that sends
    the body; binding inside the generator puts the identity in that task's context.
    """
    with bind_user(principal, toolbox_token):
        async for item in stream:
            yield item
