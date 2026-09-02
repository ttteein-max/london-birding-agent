"""Durable run management and deterministic LangGraph time travel."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from app.biodiversity.agent_models import ExpeditionRequestDraft
from app.biodiversity.models import ExpeditionRequest, RelatedTaxonCandidate, ResolvedTaxon
from app.biodiversity.run_models import (
    CheckpointSummary,
    ComparedValue,
    ForkRequest,
    ForkResult,
    PlanComparison,
    RunBranch,
)


DERIVED_FORK_FIELDS = {
    "timezone",
    "within_greater_london",
    "provenance",
    "evidence_counts",
    "executed_tool_call_audit",
    "safe_map_cells",
    "final_validated_plan",
    "terminal_status",
}


def _config(thread_id: str, checkpoint_id: str | None = None) -> dict[str, Any]:
    configurable = {"thread_id": thread_id}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _checkpoint_id(snapshot: Any) -> str:
    value = snapshot.config.get("configurable", {}).get("checkpoint_id")
    if not value:
        raise ValueError("Checkpoint does not expose a checkpoint_id")
    return str(value)


def _interrupt_kind(snapshot: Any) -> str | None:
    for task in snapshot.tasks:
        for item in task.interrupts:
            payload = item.value
            if isinstance(payload, dict) and payload.get("kind"):
                return str(payload["kind"])
    return None


def _created_at(snapshot: Any) -> datetime:
    value = snapshot.created_at
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _validate_resume_payload(
    payload: dict[str, Any],
    resume: dict[str, Any],
    *,
    state: dict[str, Any],
) -> None:
    """Reject malformed user input before LangGraph can write a resume checkpoint."""

    if not isinstance(resume, dict):
        raise ValueError("resume must be a JSON object")
    kind = payload.get("kind")
    if kind in {"taxon_selection", "related_taxon_selection"}:
        if set(resume) != {"accepted_taxon_key"}:
            raise ValueError("Resume must contain only accepted_taxon_key")
        key = resume["accepted_taxon_key"]
        allowed = {
            item.get("accepted_taxon_key") for item in payload.get("candidates", [])
        }
        if isinstance(key, bool) or not isinstance(key, int) or key not in allowed:
            raise ValueError("accepted_taxon_key is not present in the candidate list")
    elif kind == "actionable_tradeoff":
        option = resume.get("option")
        allowed = {item.get("option") for item in payload.get("options", [])}
        if not isinstance(option, str) or option not in allowed:
            raise ValueError("Resume option is not present in the deterministic option list")
        option_payload = next(
            item for item in payload["options"] if item.get("option") == option
        )
        if option == "expand_search_radius":
            radius = resume.get("search_radius_km")
            if (
                set(resume) != {"option", "search_radius_km"}
                or isinstance(radius, bool)
                or not isinstance(radius, (int, float))
                or not option_payload["current_radius_km"]
                < float(radius)
                <= option_payload["maximum_radius_km"]
            ):
                raise ValueError("Expanded radius is outside the offered range")
        elif option == "widen_seasonal_window":
            radius = resume.get("seasonal_window_radius_months")
            if (
                set(resume) != {"option", "seasonal_window_radius_months"}
                or isinstance(radius, bool)
                or not isinstance(radius, int)
                or not option_payload["current_radius_months"]
                < radius
                <= option_payload["maximum_radius_months"]
            ):
                raise ValueError("Seasonal window is outside the offered range")
        elif set(resume) != {"option"}:
            raise ValueError("This option accepts no additional fields")
    elif kind == "request_clarification":
        if set(resume) != {"updates"} or not isinstance(resume["updates"], dict):
            raise ValueError("Resume must be {'updates': {...}}")
        merged = dict(state.get("parsed_request_draft") or {})
        merged.update(resume["updates"])
        draft = ExpeditionRequestDraft.model_validate(merged)
        values = draft.model_dump(exclude_none=True)
        values["timezone"] = "Europe/London"
        ExpeditionRequest.model_validate(values)
    elif kind == "location_correction":
        if set(resume) not in ({"postcode"}, {"start_point"}):
            raise ValueError("Resume must contain exactly one location correction")
        request = ExpeditionRequest.model_validate(state["expedition_request"])
        values = request.model_dump(mode="python")
        values.update({"postcode": None, "start_point": None, **resume})
        ExpeditionRequest.model_validate(values)
    elif kind == "bird_input_correction":
        if set(resume) != {"bird_input"} or not isinstance(
            resume["bird_input"], str
        ) or not resume["bird_input"].strip():
            raise ValueError("Resume must contain only non-empty bird_input")


class BiodiversityRunManager:
    """Expose stable run contracts without leaking ``StateSnapshot`` objects."""

    def __init__(self, graph: Any, *, recorder: Any | None = None) -> None:
        self.graph = graph
        self.recorder = recorder

    def _event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.recorder is not None:
            self.recorder.record_event(event_type, payload=payload)

    def _history(self, thread_id: str) -> list[Any]:
        return list(self.graph.get_state_history(_config(thread_id)))

    def _require_thread(self, thread_id: str) -> list[Any]:
        history = self._history(thread_id)
        if not history:
            raise ValueError(f"No biodiversity run exists for thread_id={thread_id!r}")
        return history

    def _require_checkpoint(self, thread_id: str, checkpoint_id: str) -> Any:
        snapshot = next(
            (
                item
                for item in self._require_thread(thread_id)
                if _checkpoint_id(item) == checkpoint_id
            ),
            None,
        )
        if snapshot is None:
            raise ValueError(
                f"Checkpoint {checkpoint_id!r} does not exist in thread {thread_id!r}"
            )
        # Returning the history object is intentional: its config includes the
        # checkpoint namespace required by update_state and is the checkpoint's
        # own exact config, rather than a reconstructed selector.
        return snapshot

    def start(self, request: str, *, thread_id: str) -> dict[str, Any]:
        if self._history(thread_id):
            raise ValueError(
                f"thread_id={thread_id!r} already exists; start will not overwrite it"
            )
        branch_id = uuid4().hex
        result = self.graph.invoke(
            {
                "original_request_text": request,
                "branch_id": branch_id,
                "parent_branch_id": None,
                "forked_from_checkpoint_id": None,
                "fork_updates": {},
                "fork_created_at": None,
            },
            _config(thread_id),
        )
        latest = self.graph.get_state(_config(thread_id))
        self._event(
            "checkpoint_created",
            {
                "thread_id": thread_id,
                "checkpoint_id": _checkpoint_id(latest),
                "branch_id": branch_id,
            },
        )
        if _interrupt_kind(latest):
            self._event(
                "interrupt_requested",
                {
                    "thread_id": thread_id,
                    "checkpoint_id": _checkpoint_id(latest),
                    "interrupt_kind": _interrupt_kind(latest),
                },
            )
        return dict(result)

    def resume(self, *, thread_id: str, resume: dict[str, Any]) -> dict[str, Any]:
        self._require_thread(thread_id)
        snapshot = self.graph.get_state(_config(thread_id))
        kind = _interrupt_kind(snapshot)
        if kind is None:
            raise ValueError(f"Thread {thread_id!r} has no pending interrupt")
        payload = next(
            item.value
            for task in snapshot.tasks
            for item in task.interrupts
            if isinstance(item.value, dict)
        )
        _validate_resume_payload(payload, resume, state=dict(snapshot.values))
        result = self.graph.invoke(Command(resume=resume), _config(thread_id))
        latest = self.graph.get_state(_config(thread_id))
        self._event(
            "run_resumed",
            {
                "thread_id": thread_id,
                "resumed_interrupt_kind": kind,
                "checkpoint_id": _checkpoint_id(latest),
            },
        )
        next_kind = _interrupt_kind(latest)
        if next_kind:
            self._event(
                "interrupt_requested",
                {
                    "thread_id": thread_id,
                    "checkpoint_id": _checkpoint_id(latest),
                    "interrupt_kind": next_kind,
                },
            )
        return dict(result)

    def history(self, *, thread_id: str) -> list[CheckpointSummary]:
        history = self._require_thread(thread_id)
        root_branch_id = next(
            (
                str(snapshot.values["branch_id"])
                for snapshot in reversed(history)
                if snapshot.values.get("branch_id")
                and not snapshot.values.get("parent_branch_id")
            ),
            "main",
        )
        branch_finals: dict[str, str] = {}
        for snapshot in history:
            branch_id = str(snapshot.values.get("branch_id") or root_branch_id)
            if snapshot.values.get("terminal_status") and branch_id not in branch_finals:
                branch_finals[branch_id] = _checkpoint_id(snapshot)
        summaries: list[CheckpointSummary] = []
        for snapshot in history:
            values = snapshot.values
            branch_id = str(values.get("branch_id") or root_branch_id)
            parent_config = snapshot.parent_config or {}
            parent_checkpoint_id = parent_config.get("configurable", {}).get(
                "checkpoint_id"
            )
            metadata = snapshot.metadata or {}
            summaries.append(
                CheckpointSummary(
                    thread_id=thread_id,
                    branch_id=branch_id,
                    parent_branch_id=values.get("parent_branch_id"),
                    checkpoint_id=_checkpoint_id(snapshot),
                    parent_checkpoint_id=(
                        str(parent_checkpoint_id) if parent_checkpoint_id else None
                    ),
                    forked_from_checkpoint_id=values.get(
                        "forked_from_checkpoint_id"
                    ),
                    created_at=_created_at(snapshot),
                    graph_step=metadata.get("step"),
                    source=metadata.get("source"),
                    next_nodes=list(snapshot.next),
                    interrupt_kind=_interrupt_kind(snapshot),
                    terminal_status=values.get("terminal_status"),
                    applied_constraint_changes=list(
                        values.get("applied_user_decisions", [])
                    ),
                    final_checkpoint_id=branch_finals.get(branch_id),
                )
            )
        return summaries

    def branches(self, *, thread_id: str) -> list[RunBranch]:
        history = self._require_thread(thread_id)
        heads: dict[str, Any] = {}
        finals: dict[str, str] = {}
        for snapshot in history:
            if not snapshot.values.get("branch_id"):
                continue
            branch_id = str(snapshot.values["branch_id"])
            heads.setdefault(branch_id, snapshot)
            if snapshot.values.get("terminal_status"):
                finals.setdefault(branch_id, _checkpoint_id(snapshot))
        return [
            RunBranch(
                thread_id=thread_id,
                branch_id=branch_id,
                parent_branch_id=snapshot.values.get("parent_branch_id"),
                head_checkpoint_id=_checkpoint_id(snapshot),
                final_checkpoint_id=finals.get(branch_id),
                forked_from_checkpoint_id=snapshot.values.get(
                    "forked_from_checkpoint_id"
                ),
                fork_updates=dict(snapshot.values.get("fork_updates") or {}),
                created_at=datetime.fromisoformat(
                    str(
                        snapshot.values.get("fork_created_at")
                        or _created_at(snapshot).isoformat()
                    ).replace("Z", "+00:00")
                ),
                terminal_status=snapshot.values.get("terminal_status"),
            )
            for branch_id, snapshot in heads.items()
        ]

    def replay(self, *, thread_id: str, checkpoint_id: str) -> dict[str, Any]:
        snapshot = self._require_checkpoint(thread_id, checkpoint_id)
        self._event(
            "checkpoint_selected",
            {"thread_id": thread_id, "checkpoint_id": checkpoint_id},
        )
        self._event(
            "replay_started",
            {"thread_id": thread_id, "checkpoint_id": checkpoint_id},
        )
        try:
            result = self.graph.invoke(None, snapshot.config)
        except Exception as exc:
            self._event(
                "replay_failed",
                {
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        self._event(
            "replay_completed",
            {"thread_id": thread_id, "checkpoint_id": checkpoint_id},
        )
        if result.get("__interrupt__"):
            value = result["__interrupt__"][0].value
            self._event(
                "interrupt_requested",
                {
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                    "interrupt_kind": (
                        value.get("kind") if isinstance(value, dict) else "unknown"
                    ),
                },
            )
        return dict(result)

    @staticmethod
    def _selected_related_taxon(values: dict[str, Any], key: int) -> dict[str, Any]:
        candidates = list(values.get("related_taxon_candidates") or [])
        if not candidates:
            payload = values.get("pending_hitl_payload") or {}
            candidates = list(payload.get("candidates") or [])
        for raw in candidates:
            item = dict(raw)
            if "class" in item and "class_name" not in item:
                item["class_name"] = item.pop("class")
            candidate = RelatedTaxonCandidate.model_validate(item)
            if candidate.accepted_taxon_key == key:
                return candidate.model_dump(mode="json")
        raise ValueError(
            "selected_related_taxon_key must be present in validated related candidates"
        )

    @classmethod
    def _fork_values(cls, snapshot: Any, request: ForkRequest) -> dict[str, Any]:
        values = snapshot.values
        if not all(
            values.get(name)
            for name in ("expedition_request", "resolved_location", "resolved_taxon")
        ):
            raise ValueError(
                "Fork updates require a checkpoint after request, location, and taxonomy resolution"
            )
        previous_request = ExpeditionRequest.model_validate(values["expedition_request"])
        raw_updates = request.updates.model_dump(mode="python", exclude_none=True)
        selected_key = raw_updates.pop("selected_related_taxon_key", None)
        updated_request_values = previous_request.model_dump(mode="python")
        updated_request_values.update(raw_updates)
        selected_taxon: dict[str, Any] | None = None
        if selected_key is not None:
            selected_taxon = cls._selected_related_taxon(values, selected_key)
            updated_request_values["bird_input"] = (
                selected_taxon.get("common_name")
                or selected_taxon["canonical_name"]
            )
        updated_request = ExpeditionRequest.model_validate(updated_request_values)

        changed = {
            name
            for name, new_value in raw_updates.items()
            if getattr(previous_request, name) != new_value
        }
        invalidated: set[str] = set()
        if "search_radius_km" in changed:
            invalidated.add("public_sites")
        if {"seasonal_window_radius_months", "target_month_override"}.intersection(
            changed
        ) or selected_taxon:
            invalidated.update({"occurrence", "public_sites"})
        if "target_local_date" in changed:
            invalidated.add("weather")
            if updated_request.target_month_override is None:
                invalidated.update({"occurrence", "public_sites"})

        branch_id = uuid4().hex
        fork_updates = request.updates.model_dump(mode="json", exclude_none=True)
        update: dict[str, Any] = {
            "expedition_request": updated_request.model_dump(mode="json"),
            "branch_id": branch_id,
            "parent_branch_id": str(values.get("branch_id") or "main"),
            "forked_from_checkpoint_id": request.checkpoint_id,
            "fork_updates": fork_updates,
            "fork_created_at": datetime.now(UTC).isoformat(),
            "branch_label": request.branch_label,
            "invalidated_evidence": sorted(invalidated),
            "decision_route": (
                "refresh_invalidated_evidence"
                if invalidated
                else "deterministic_validation"
            ),
            "deterministic_constraints": [],
            "evidence_bundle": None,
            "deterministic_plan_status": None,
            "deterministic_phase1_plan": None,
            "draft_llm_plan": None,
            "final_validated_plan": None,
            "grounding_errors": [],
            "pending_hitl_kind": None,
            "pending_hitl_payload": None,
            "pending_user_choice": None,
            "terminal_status": None,
            "terminal_result": None,
            "applied_user_decisions": [
                {
                    "kind": "fork_update",
                    "option": "fork_update",
                    "updates": fork_updates,
                }
            ],
        }
        if "occurrence" in invalidated:
            update["occurrence_evidence"] = None
        if "weather" in invalidated:
            update["weather_evidence"] = None
        if "public_sites" in invalidated:
            update["public_site_search"] = None
        if selected_taxon:
            previous_taxon = ResolvedTaxon.model_validate(values["resolved_taxon"])
            update["resolved_taxon"] = ResolvedTaxon(
                status="resolved",
                original_input=updated_request.bird_input,
                normalised_input=selected_taxon["canonical_name"].casefold(),
                provenance=previous_taxon.provenance,
                rationale=(
                    "Selected from the validated related-taxon candidates during a fork."
                ),
                **{
                    key: selected_taxon.get(key)
                    for key in (
                        "accepted_taxon_key",
                        "common_name",
                        "scientific_name",
                        "canonical_name",
                        "rank",
                        "taxonomic_status",
                        "class_name",
                        "order",
                        "family",
                        "genus",
                        "resolution_method",
                        "confidence",
                    )
                },
            ).model_dump(mode="json")
        return update

    def fork(self, request: ForkRequest) -> ForkResult:
        snapshot = self._require_checkpoint(request.thread_id, request.checkpoint_id)
        values = self._fork_values(snapshot, request)
        branch_id = values["branch_id"]
        self._event(
            "checkpoint_selected",
            {
                "thread_id": request.thread_id,
                "checkpoint_id": request.checkpoint_id,
            },
        )
        try:
            fork_config = self.graph.update_state(
                snapshot.config,
                values=values,
                as_node="apply_validated_user_choice",
            )
            fork_checkpoint_id = str(
                fork_config["configurable"]["checkpoint_id"]
            )
            self._event(
                "fork_created",
                {
                    "thread_id": request.thread_id,
                    "branch_id": branch_id,
                    "parent_branch_id": values["parent_branch_id"],
                    "forked_from_checkpoint_id": request.checkpoint_id,
                    "checkpoint_id": fork_checkpoint_id,
                },
            )
            self._event(
                "fork_started",
                {
                    "thread_id": request.thread_id,
                    "branch_id": branch_id,
                    "checkpoint_id": fork_checkpoint_id,
                },
            )
            self.graph.invoke(None, fork_config)
        except Exception as exc:
            self._event(
                "fork_failed",
                {
                    "thread_id": request.thread_id,
                    "branch_id": branch_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        branch_history = [
            item
            for item in self.graph.get_state_history(_config(request.thread_id))
            if item.values.get("branch_id") == branch_id
        ]
        head = branch_history[0]
        final_id = (
            _checkpoint_id(head) if head.values.get("terminal_status") else None
        )
        interrupt_kind = _interrupt_kind(head)
        self._event(
            "fork_completed",
            {
                "thread_id": request.thread_id,
                "branch_id": branch_id,
                "checkpoint_id": _checkpoint_id(head),
                "terminal_status": head.values.get("terminal_status"),
                "interrupt_kind": interrupt_kind,
            },
        )
        if interrupt_kind:
            self._event(
                "interrupt_requested",
                {
                    "thread_id": request.thread_id,
                    "checkpoint_id": _checkpoint_id(head),
                    "interrupt_kind": interrupt_kind,
                },
            )
        return ForkResult(
            thread_id=request.thread_id,
            branch_id=branch_id,
            parent_branch_id=values["parent_branch_id"],
            forked_from_checkpoint_id=request.checkpoint_id,
            fork_checkpoint_id=fork_checkpoint_id,
            final_checkpoint_id=final_id,
            terminal_status=head.values.get("terminal_status"),
            interrupt_kind=interrupt_kind,
            applied_updates=request.updates.model_dump(mode="json", exclude_none=True),
        )

    @staticmethod
    def _comparison_values(values: dict[str, Any]) -> dict[str, Any]:
        request = values.get("expedition_request") or {}
        taxon = values.get("resolved_taxon") or {}
        occurrence = values.get("occurrence_evidence") or {}
        weather = values.get("weather_evidence") or {}
        plan = values.get("final_validated_plan") or {}
        bundle = values.get("evidence_bundle") or {}
        return {
            "request_constraints": {
                key: request.get(key)
                for key in (
                    "target_local_date",
                    "duration_hours",
                    "rain_preference",
                    "target_month_override",
                    "seasonal_window_radius_months",
                    "search_radius_km",
                )
            },
            "selected_taxon": {
                key: taxon.get(key)
                for key in (
                    "accepted_taxon_key",
                    "common_name",
                    "canonical_name",
                )
            },
            "evidence_outcome": occurrence.get("outcome"),
            "weather_status": weather.get("status"),
            "plan_status": plan.get("status")
            or values.get("deterministic_plan_status"),
            "recommended_site_ids": sorted(
                item.get("site_id") for item in plan.get("recommended_sites", [])
            ),
            "contextual_site_ids": sorted(
                item.get("site_id") for item in plan.get("contextual_sites", [])
            ),
            "limitations": sorted(
                plan.get("unresolved_limitations")
                or bundle.get("safety_and_scientific_limitations")
                or []
            ),
            "provenance_sources": sorted(
                {
                    item.get("source")
                    for item in bundle.get("provenance", [])
                    if item.get("source")
                }
            ),
            "applied_user_decisions": list(
                values.get("applied_user_decisions", [])
            ),
        }

    def compare(
        self,
        *,
        thread_id: str,
        checkpoint_a: str,
        checkpoint_b: str,
    ) -> PlanComparison:
        first = self._require_checkpoint(thread_id, checkpoint_a)
        second = self._require_checkpoint(thread_id, checkpoint_b)
        left = self._comparison_values(first.values)
        right = self._comparison_values(second.values)
        fields = list(left)
        compared = {
            name: ComparedValue(
                checkpoint_a=_json_value(left[name]),
                checkpoint_b=_json_value(right[name]),
                changed=left[name] != right[name],
            )
            for name in fields
        }
        output = PlanComparison(
            thread_id=thread_id,
            checkpoint_a=checkpoint_a,
            checkpoint_b=checkpoint_b,
            **compared,
            changed_fields=[name for name in fields if left[name] != right[name]],
        )
        self._event(
            "comparison_created",
            {
                "thread_id": thread_id,
                "checkpoint_a": checkpoint_a,
                "checkpoint_b": checkpoint_b,
                "changed_fields": output.changed_fields,
            },
        )
        return output
