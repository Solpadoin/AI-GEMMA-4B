from __future__ import annotations

from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from agent_runtime import AgentRuntime


class AgentGraphState(TypedDict, total=False):
    project: dict[str, Any]
    settings: dict[str, Any]
    step: int
    max_steps: int
    assistant_text: str
    action: dict[str, Any] | None
    result: str
    final: str
    pending_action: dict[str, Any] | None
    protocol_error: str
    unrestricted_paths: bool


ToolCallback = Callable[[dict[str, str]], None]
CompletionFn = Callable[[list[dict[str, str]], dict[str, Any]], str]
NowFn = Callable[[], str]
EvidenceGuardFn = Callable[[dict[str, Any], dict[str, Any]], str | None]


class LangGraphAgentRunner:
    def __init__(
        self,
        runtime: AgentRuntime,
        completion: CompletionFn,
        now_iso: NowFn,
        final_guard: EvidenceGuardFn,
    ) -> None:
        self.runtime = runtime
        self.completion = completion
        self.now_iso = now_iso
        self.final_guard = final_guard
        self.graph = self._build_graph()

    def run(
        self,
        project: dict[str, Any],
        settings: dict[str, Any],
        on_tool: ToolCallback | None = None,
    ) -> dict[str, Any]:
        state: AgentGraphState = {
            "project": project,
            "settings": settings,
            "step": 0,
            "max_steps": int(settings.get("max_tool_steps", 8)),
            "unrestricted_paths": settings.get("access_mode") == "auto_all",
        }
        result = self.graph.invoke(state, config={"recursion_limit": max(25, int(settings.get("max_tool_steps", 8)) * 5)})
        for event in result.get("_tool_events", []):
            if on_tool:
                on_tool(event)
        return result

    def _build_graph(self):
        graph = StateGraph(AgentGraphState)
        graph.add_node("select_action", self._select_action)
        graph.add_node("protocol_error", self._protocol_error)
        graph.add_node("execute_tool", self._execute_tool)
        graph.add_node("finish", self._finish)
        graph.add_node("pending", self._pending)
        graph.add_node("limit", self._limit)

        graph.add_edge(START, "select_action")
        graph.add_conditional_edges(
            "select_action",
            self._route_action,
            {
                "protocol_error": "protocol_error",
                "execute_tool": "execute_tool",
                "finish": "finish",
                "pending": "pending",
                "limit": "limit",
            },
        )
        graph.add_edge("protocol_error", "select_action")
        graph.add_conditional_edges("execute_tool", self._route_after_tool, {"select_action": "select_action", "limit": "limit"})
        graph.add_edge("finish", END)
        graph.add_edge("pending", END)
        graph.add_edge("limit", END)
        return graph.compile()

    def _select_action(self, state: AgentGraphState) -> dict[str, Any]:
        project = state["project"]
        settings = state["settings"]
        messages = self.runtime.build_action_messages(project, settings)
        assistant_text = self.completion(messages, settings)
        action = self.runtime.parse_action(assistant_text)
        if action:
            project["messages"].append(
                {
                    "role": "assistant",
                    "content": assistant_text,
                    "created_at": self.now_iso(),
                    "hidden": True,
                }
            )
        return {"project": project, "assistant_text": assistant_text, "action": action}

    def _route_action(self, state: AgentGraphState) -> str:
        if int(state.get("step", 0)) >= int(state.get("max_steps", 8)):
            return "limit"
        action = state.get("action")
        if not action:
            return "protocol_error"
        if action.get("tool") == "final":
            error = self.final_guard(state["project"], action)
            if error:
                return "protocol_error"
            return "finish"
        if not self.runtime.can_auto_execute(action, state["settings"]):
            return "pending"
        return "execute_tool"

    def _protocol_error(self, state: AgentGraphState) -> dict[str, Any]:
        project = state["project"]
        action = state.get("action")
        error = "expected one JSON action object. Choose a tool or final."
        if action and action.get("tool") == "final":
            error = self.final_guard(project, action) or error
        project["messages"].append(
            {
                "role": "tool",
                "content": "Protocol error: " + error,
                "created_at": self.now_iso(),
                "hidden": True,
            }
        )
        return {"project": project, "step": int(state.get("step", 0)) + 1, "protocol_error": error}

    def _execute_tool(self, state: AgentGraphState) -> dict[str, Any]:
        project = state["project"]
        action = state["action"] or {}
        try:
            result = self.runtime.execute_action(action, unrestricted_paths=bool(state.get("unrestricted_paths")))
        except Exception as exc:  # noqa: BLE001 - convert all tool failures into tool context.
            result = f"Tool error for {action.get('tool', 'tool')}: {exc}"
        self.runtime.append_tool_result(project, action, result, self.now_iso)
        event = self.runtime.public_tool_event(action, result)
        events = list(state.get("_tool_events", []))
        events.append(event)
        return {
            "project": project,
            "result": result,
            "_tool_events": events,
            "step": int(state.get("step", 0)) + 1,
        }

    def _route_after_tool(self, state: AgentGraphState) -> str:
        if int(state.get("step", 0)) >= int(state.get("max_steps", 8)):
            return "limit"
        return "select_action"

    def _finish(self, state: AgentGraphState) -> dict[str, Any]:
        project = state["project"]
        action = state["action"] or {}
        answer = str(action.get("answer", ""))
        project["messages"].append({"role": "assistant", "content": answer, "created_at": self.now_iso()})
        project["pending_action"] = None
        return {"project": project, "final": answer}

    def _pending(self, state: AgentGraphState) -> dict[str, Any]:
        project = state["project"]
        action = state.get("action")
        project["pending_action"] = action
        return {"project": project, "pending_action": action}

    def _limit(self, state: AgentGraphState) -> dict[str, Any]:
        project = state["project"]
        project["messages"].append(
            {
                "role": "assistant",
                "content": "Reached max tool steps for this turn.",
                "created_at": self.now_iso(),
            }
        )
        return {"project": project, "final": "Reached max tool steps for this turn."}
