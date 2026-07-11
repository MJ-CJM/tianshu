"""Worker — wraps single-node Agent execution with tool/context trimming."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from tianshu.executor.adapters import PreparedExecutor
from tianshu.executor.agent import Agent, AgentResult
from tianshu.models.common import TaskStatus
from tianshu.models.dag import DAGNode
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial
from tianshu.persona.model import AgentPersona
from tianshu.persona.prompt_builder import PromptBuilder
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class Worker:
    """Executes a single DAG node using an Agent instance."""

    def __init__(
        self,
        agent: Agent,
        storage: Storage,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self._agent = agent
        self._storage = storage
        self._prompt_builder = prompt_builder

    async def execute_node(
        self,
        edict: Edict,
        node: DAGNode,
        upstream_results: dict[str, str],
        persona: AgentPersona | None = None,
        checkpoint_manager: object | None = None,
        prepared_executor: PreparedExecutor | None = None,
    ) -> AgentResult:
        """Execute a single DAG node.

        Args:
            edict: The parent edict.
            node: The DAG node to execute.
            upstream_results: Results from upstream nodes {node_id: result_text}.
            persona: Optional persona for this worker.
            checkpoint_manager: Optional checkpoint manager for saving state.
        """
        # Create memorial for this node
        memorial = Memorial(
            edict_id=edict.id,
            instruction=node.description,
            status=TaskStatus.SUBMITTED,
            dag_node_id=node.node_id,
        )
        if persona:
            memorial.persona_id = persona.id
        self._storage.save_memorial(memorial)
        node.memorial_id = memorial.id

        node_executor = (
            prepared_executor.bind_run(memorial.id, instruction=node.description)
            if prepared_executor
            else None
        )
        if node_executor is not None:
            self._storage.save_effective_governance_contract(
                memorial.id,
                edict.id,
                node_executor.effective,
            )
            memorial.effective_governance_contract = node_executor.effective

        memorial.status = TaskStatus.RUNNING
        memorial.started_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)

        # Build upstream context
        history: list[dict] | None = None
        if upstream_results:
            context_parts = [
                f"[Upstream node {nid} result]: {result}"
                for nid, result in upstream_results.items()
            ]
            history = [{"role": "system", "content": "\n\n".join(context_parts)}]

        # Build user content with node description
        user_content = node.description

        # Tool filter
        tool_filter = node.tools_required if node.tools_required else None

        def on_event(event: dict) -> None:
            self._storage.append_event(edict.id, memorial.id, event["type"], event)

        try:
            executor = node_executor or self._agent
            result = await executor.execute(
                edict,
                memorial=memorial,
                on_event=on_event,
                history=history,
                user_content=user_content,
                tool_filter=tool_filter,
                persona=persona,
            )
            memorial.status = result.status
            memorial.summary = result.summary
            memorial.result = result.result
            memorial.usage = result.usage
            memorial.error = result.error
        except asyncio.CancelledError:
            memorial.status = TaskStatus.CANCELLED
            memorial.error = "Node execution cancelled"
            result = AgentResult(
                status=TaskStatus.CANCELLED,
                error="Node execution cancelled",
            )
            raise
        except Exception as e:
            memorial.status = TaskStatus.FAILED
            memorial.error = str(e)
            result = AgentResult(
                status=TaskStatus.FAILED,
                error=str(e),
            )
        finally:
            memorial.completed_at = datetime.now(UTC)
            self._storage.update_memorial(memorial)

        return result
