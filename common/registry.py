"""Discovery of the agents shipped in the ``agents/`` package.

Every sub-package ``agents/<name>/`` must expose ``create_agent() -> BaseAgent`` in its
``agent.py`` module. The ``AGENTS`` env var (comma-separated names) restricts the agents
an image serves; by default every discovered agent is available.
"""

import importlib
import logging
import os
import pkgutil

from common.agent import BaseAgent

logger = logging.getLogger(__name__)


class UnknownAgentError(LookupError):
    pass


class AgentRegistry:
    def __init__(self, package: str = "agents"):
        self._package = package
        self._instances: dict[str, BaseAgent] = {}

    def discovered_agents(self) -> list[str]:
        package = importlib.import_module(self._package)
        return sorted(
            info.name
            for info in pkgutil.iter_modules(package.__path__)
            if info.ispkg and not info.name.startswith("_")
        )

    def available_agents(self) -> list[str]:
        discovered = self.discovered_agents()
        selected = [n.strip() for n in os.getenv("AGENTS", "").split(",") if n.strip()]
        if not selected:
            return sorted(set(discovered) | set(self._instances))
        unknown = [n for n in selected if n not in discovered and n not in self._instances]
        if unknown:
            logger.warning("AGENTS lists unknown agent(s): %s", ", ".join(unknown))
        return [n for n in selected if n in discovered or n in self._instances]

    def get_agent(self, name: str) -> BaseAgent:
        if name in self._instances:
            return self._instances[name]
        if name not in self.available_agents():
            raise UnknownAgentError(f"Unknown agent {name!r}; available: {self.available_agents()}")
        module = importlib.import_module(f"{self._package}.{name}.agent")
        agent = module.create_agent()
        if not isinstance(agent, BaseAgent):
            raise TypeError(f"{module.__name__}.create_agent() must return a BaseAgent, got {type(agent).__name__}")
        self._instances[name] = agent
        return agent

    def register(self, agent: BaseAgent) -> BaseAgent:
        # Pre-register an instance (tests, custom wiring). Bypasses discovery.
        self._instances[agent.name] = agent
        return agent

    def describe_agents(self) -> list[dict]:
        result = []
        for name in self.available_agents():
            try:
                agent = self.get_agent(name)
            except Exception as exc:  # noqa: BLE001 — one broken agent must not hide the others
                logger.exception("Agent %r failed to load", name)
                result.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
                continue
            result.append({
                "name": name,
                "display_name": agent.display_name,
                "description": agent.description,
            })
        return result

    async def aclose_all(self) -> None:
        for agent in self._instances.values():
            await agent.aclose()

    def reset(self) -> None:
        self._instances = {}


registry = AgentRegistry()
