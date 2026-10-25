"""The order agent as a LangGraph state graph.

interpret -> resolve_products -> question: answer_question -> END
                              -> other:    handoff -> END
                              -> order:    update_draft -> check_business_rules -> save_order
save_order -> needs staff:  hold_reply -> staff_review (pauses) -> compose_reply -> END
           -> otherwise:    compose_reply -> END
"""

from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent import nodes
from app.agent.routing import STAFF_REVIEW
from app.agent.state import AgentDeps, AgentState

type AgentGraph = CompiledStateGraph[AgentState, AgentDeps, AgentState, AgentState]


def _after_resolve(state: AgentState) -> str:
    intent = state.get("intent")
    if intent == "question":
        return "answer_question"
    if intent == "other":
        return "handoff"
    return "update_draft"


def _after_save(state: AgentState) -> str:
    return "hold_reply" if state.get("route") == STAFF_REVIEW else "compose_reply"


def build_graph(checkpointer: BaseCheckpointSaver[Any]) -> AgentGraph:
    g = StateGraph(AgentState, context_schema=AgentDeps)
    g.add_node("interpret", cast(Any, nodes.interpret))
    g.add_node("resolve_products", cast(Any, nodes.resolve_products))
    g.add_node("answer_question", cast(Any, nodes.answer_question))
    g.add_node("handoff", cast(Any, nodes.handoff))
    g.add_node("update_draft", cast(Any, nodes.update_draft))
    g.add_node("check_business_rules", cast(Any, nodes.check_business_rules))
    g.add_node("save_order", cast(Any, nodes.save_order))
    g.add_node("hold_reply", cast(Any, nodes.hold_reply))
    g.add_node("staff_review", cast(Any, nodes.staff_review))
    g.add_node("compose_reply", cast(Any, nodes.compose_reply))

    g.add_edge(START, "interpret")
    g.add_edge("interpret", "resolve_products")
    g.add_conditional_edges("resolve_products", _after_resolve)
    g.add_edge("answer_question", END)
    g.add_edge("handoff", END)
    g.add_edge("update_draft", "check_business_rules")
    g.add_edge("check_business_rules", "save_order")
    g.add_conditional_edges("save_order", _after_save)
    g.add_edge("hold_reply", "staff_review")
    g.add_edge("staff_review", "compose_reply")
    g.add_edge("compose_reply", END)
    return g.compile(checkpointer=checkpointer)


@dataclass
class AgentOutcome:
    state: dict[str, Any]
    paused: bool  # waiting for staff review

    @property
    def reply(self) -> str | None:
        reply: str | None = self.state.get("reply")
        return reply


def _config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


async def run_message(
    graph: AgentGraph,
    deps: AgentDeps,
    *,
    thread_id: str,
    business_id: int,
    customer_id: int,
    text: str,
    message_id: int | None = None,
) -> AgentOutcome:
    initial: AgentState = {
        "business_id": business_id,
        "customer_id": customer_id,
        "message_id": message_id,
        "text": text,
    }
    result = await graph.ainvoke(initial, _config(thread_id), context=deps)
    return AgentOutcome(dict(result), paused="__interrupt__" in result)


async def resume_review(
    graph: AgentGraph, deps: AgentDeps, *, thread_id: str, decision: dict[str, Any]
) -> AgentOutcome:
    """Continue a paused run with the staff decision: {"action": "approve" | "reject"}."""
    command: Command[Any] = Command(resume=decision)
    result = await graph.ainvoke(command, _config(thread_id), context=deps)
    return AgentOutcome(dict(result), paused="__interrupt__" in result)
