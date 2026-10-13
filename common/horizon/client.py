"""OpenSearch connection settings and client factory for the Horizon topic index."""

import asyncio
import os
from dataclasses import dataclass

from common.horizon.mapping import DEFAULT_INDEX, RRF_PIPELINE


@dataclass(frozen=True)
class HorizonSettings:
    url: str
    index: str
    search_pipeline: str
    user: str | None = None
    password: str | None = None

    @classmethod
    def from_env(cls) -> "HorizonSettings":
        return cls(
            url=os.environ.get("HORIZON_OS_URL", "http://localhost:9200"),
            index=os.environ.get("HORIZON_OS_INDEX", DEFAULT_INDEX),
            search_pipeline=os.environ.get("HORIZON_SEARCH_PIPELINE", RRF_PIPELINE),
            user=os.environ.get("HORIZON_OS_USER") or None,
            password=os.environ.get("HORIZON_OS_PASSWORD") or None,
        )


def build_client(settings: HorizonSettings | None = None):
    # Imported lazily: opensearch-py belongs to the optional "topic-matching" dependency group.
    from opensearchpy import AsyncOpenSearch

    settings = settings or HorizonSettings.from_env()
    kwargs: dict = {"hosts": [settings.url], "use_ssl": settings.url.startswith("https://"), "verify_certs": False}
    if settings.user:
        kwargs["http_auth"] = (settings.user, settings.password or "")
    return AsyncOpenSearch(**kwargs)


class LoopBoundOpenSearch:
    """An ``AsyncOpenSearch`` proxy that opens one client per running event loop.

    The OpenWebUI adapter drives agents through ``BaseAgent.stream``, which runs every turn in a fresh event loop
    and closes it afterwards; an aiohttp session created during the first turn is then unusable ("Event loop is
    closed"). This proxy creates the client on first use in each loop and ``close()`` releases the current loop's
    client, so callers close it at the end of every turn (see ``ProjectTopicMatchingAgent.astream``).
    """

    def __init__(self, settings: HorizonSettings | None = None, factory=None):
        self.settings = settings or HorizonSettings.from_env()
        self._factory = factory or build_client
        self._clients: dict[asyncio.AbstractEventLoop, object] = {}

    def _current(self):
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None:
            # Drop the clients of loops that are gone (they cannot be closed any more).
            self._clients = {l: c for l, c in self._clients.items() if not l.is_closed()}
            client = self._factory(self.settings)
            self._clients[loop] = client
        return client

    def __getattr__(self, name: str):
        return getattr(self._current(), name)

    async def close(self) -> None:
        client = self._clients.pop(asyncio.get_running_loop(), None)
        if client is not None:
            await client.close()
