"""OpenSearch connection settings and client factory for the Horizon topic index."""

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
