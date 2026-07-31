"""Runner selection.

`AGENT_RUNNER` picks the implementation. Everything else in the application depends only on the
`AgentRunner` protocol, so swapping frameworks touches this file and one module.

The runner is built once per process (in the application lifespan) because constructing it
creates the model client. It holds no per-conversation state: everything that varies between
requests travels in `AgentDeps`.
"""

from __future__ import annotations

from app.agent.contracts import AgentRunner
from app.core.config import Settings
from app.core.errors import AgentExecutionError
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_runner(settings: Settings) -> AgentRunner:
    if settings.agent_runner == "pydantic_ai":
        from app.agent.pydantic_ai_runner import PydanticAIRunner

        return PydanticAIRunner(settings=settings)

    if settings.agent_runner == "langgraph":
        try:
            from app.agent.langgraph_runner import LangGraphRunner
        except ImportError as exc:  # pragma: no cover - optional extra
            raise AgentExecutionError(
                "Runner LangGraph indisponivel: instale as dependencias opcionais com "
                'pip install -e ".[langgraph]".',
                code="AGENT_RUNNER_UNAVAILABLE",
            ) from exc

        runner: AgentRunner = LangGraphRunner(settings=settings)
        return runner

    raise AgentExecutionError(  # pragma: no cover - Settings already restricts the values
        f"Runner desconhecido: {settings.agent_runner!r}.",
        code="AGENT_RUNNER_UNKNOWN",
    )
