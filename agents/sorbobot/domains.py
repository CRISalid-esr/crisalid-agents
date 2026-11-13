"""Domain exploration: what research domains cover (§5.4 of the spec)."""

import asyncio

from agents.sorbobot.config import SorbobotSettings
from agents.sorbobot.topics import TopicFinder


class DomainExploration:
    def __init__(self, finder: TopicFinder, toolbox, settings: SorbobotSettings):
        self._finder = finder
        self._toolbox = toolbox
        self._settings = settings

    async def run(self, topic_text: str | None, domain_names: list[str] | None) -> dict:
        if domain_names:
            topics = await self._by_names(domain_names)
            subject = None
        else:
            selection = await self._finder.select(topic_text or "")
            if selection.hint:
                return {"topic": topic_text, "domains": [], "hint": selection.hint}
            topics = sorted(selection.kept, key=lambda t: (-(t.judge_score or 0.0), -(t.similarity or 0.0)))
            subject = topic_text
        if not topics:
            return {"topic": subject, "domains": [], "hint": "no_domain"}
        shown = topics[: self._settings.explore_max_domains]
        return {"topic": subject, "domains": [topic.as_domain(number).as_dict() for number, topic in enumerate(shown, 1)]}

    async def _by_names(self, names: list[str]):
        """The Topic concepts whose label is one of the names, in the order of the names; unknown names are dropped."""
        wanted = [str(name).strip() for name in names if str(name).strip()]
        found = await asyncio.gather(*(self._toolbox.search_concepts_by_name(name) for name in wanted))
        uids: list[str] = []
        for rows in found:
            for row in rows:
                if row.get("type") == "Topic" and row["uid"] not in uids:
                    uids.append(row["uid"])
        return await self._finder.describe(uids)
