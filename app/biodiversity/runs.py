"""Durable run management and deterministic LangGraph time travel."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from app.biodiversity.agent_models import ExpeditionRequestDraft
from app.biodiversity.models import (
    EvidenceItem,
    EvidenceUse,
    ExpeditionRequest,
    RelatedTaxonCandidate,
    ResolvedTaxon,
)
from app.biodiversity.run_models import (
    CheckpointSummary,
    ComparedValue,
    ForkRequest,
    ForkResult,
    PlanComparison,
    ReplayResult,
    RunBranch,
    RunExecution,
    RunManifest,
    RunProfile,
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


def _metadata(snapshot: Any) -> dict[str, Any]:
    return dict(snapshot.metadata or {})


def _execution_id(snapshot: Any) -> str:
    value = _metadata(snapshot).get("execution_id") or snapshot.values.get(
        "execution_id"
    )
    return str(value or snapshot.values.get("branch_id") or "main")


def _parent_execution_id(snapshot: Any) -> str | None:
    value = _metadata(snapshot).get("parent_execution_id")
    if value is None:
        value = snapshot.values.get("parent_execution_id")
    return str(value) if value else None


def _replayed_from_checkpoint_id(snapshot: Any) -> str | None:
    value = _metadata(snapshot).get("replayed_from_checkpoint_id")
    if value is None:
        value = snapshot.values.get("replayed_from_checkpoint_id")
    return str(value) if value else None


def _execution_created_at(snapshot: Any) -> datetime:
    value = _metadata(snapshot).get("execution_created_at") or snapshot.values.get(
        "execution_created_at"
    )
    if value:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _created_at(snapshot)


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

    def __init__(
        self,
        graph: Any,
        *,
        recorder: Any | None = None,
        run_profile: RunProfile | dict[str, Any] | None = None,
    ) -> None:
        self.graph = graph
        self.recorder = recorder
        self.run_profile = RunProfile.model_validate(
            run_profile or RunProfile()
        )

    def _runtime_config(
        self,
        config: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        output = dict(config)
        output["configurable"] = dict(config.get("configurable") or {})
        merged_metadata = dict(config.get("metadata") or {})
        merged_metadata.update(metadata or {})
        if merged_metadata:
            output["metadata"] = merged_metadata
        if self.recorder is not None:
            callbacks = list(config.get("callbacks") or [])
            if self.recorder not in callbacks:
                callbacks.append(self.recorder)
            output["callbacks"] = callbacks
        return output

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

    def manifest(self, *, thread_id: str) -> RunManifest | None:
        history = self._require_thread(thread_id)
        raw = next(
            (
                snapshot.values.get("run_manifest")
                for snapshot in history
                if snapshot.values.get("run_manifest")
            ),
            None,
        )
        return RunManifest.model_validate(raw) if raw else None

    def _validate_run_profile(self, snapshot: Any) -> None:
        raw = snapshot.values.get("run_manifest")
        if not raw:
            return
        stored = RunManifest.model_validate(raw)
        current = self.run_profile.model_dump(mode="json")
        expected = stored.model_dump(mode="json", exclude={"created_at"})
        if current != expected:
            changed = sorted(
                key for key in current if current.get(key) != expected.get(key)
            )
            raise ValueError(
                "Runtime profile does not match the saved run manifest: "
                + ", ".join(changed)
            )

    def snapshot(self, *, thread_id: str, checkpoint_id: str) -> Any:
        return self._require_checkpoint(thread_id, checkpoint_id)

    def execution_head(self, *, thread_id: str, execution_id: str) -> Any:
        snapshot = next(
            (
                snapshot
                for snapshot in self._require_thread(thread_id)
                if _execution_id(snapshot) == execution_id
            ),
            None,
        )
        if snapshot is None:
            raise ValueError(
                f"Execution {execution_id!r} does not exist in thread {thread_id!r}"
            )
        return snapshot

    def start(self, request: str, *, thread_id: str) -> dict[str, Any]:
        if self._history(thread_id):
            raise ValueError(
                f"thread_id={thread_id!r} already exists; start will not overwrite it"
            )
        branch_id = uuid4().hex
        execution_id = uuid4().hex
        created_at = datetime.now(UTC).isoformat()
        manifest = RunManifest(
            **self.run_profile.model_dump(mode="python"),
            created_at=datetime.now(UTC),
        )
        result = self.graph.invoke(
            {
                "original_request_text": request,
                "run_manifest": manifest.model_dump(mode="json"),
                "branch_id": branch_id,
                "branch_created_at": created_at,
                "parent_branch_id": None,
                "forked_from_checkpoint_id": None,
                "fork_updates": {},
                "fork_created_at": None,
                "execution_id": execution_id,
                "parent_execution_id": None,
                "replayed_from_checkpoint_id": None,
                "execution_created_at": created_at,
                "low_confidence_accepted": False,
                "related_taxon_source_key": None,
            },
            self._runtime_config(
                _config(thread_id),
                metadata={
                    "execution_id": execution_id,
                    "execution_created_at": created_at,
                    "branch_id": branch_id,
                },
            ),
        )
        latest = self.execution_head(
            thread_id=thread_id, execution_id=execution_id
        )
        self._event(
            "checkpoint_created",
            {
                "thread_id": thread_id,
                "checkpoint_id": _checkpoint_id(latest),
                "branch_id": branch_id,
                "execution_id": execution_id,
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

    def _pending_execution_heads(self, thread_id: str) -> list[Any]:
        heads: dict[str, Any] = {}
        for snapshot in self._require_thread(thread_id):
            heads.setdefault(_execution_id(snapshot), snapshot)
        return [snapshot for snapshot in heads.values() if _interrupt_kind(snapshot)]

    def resume(
        self,
        *,
        thread_id: str,
        resume: dict[str, Any],
        checkpoint_id: str | None = None,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        pending = self._pending_execution_heads(thread_id)
        if checkpoint_id:
            snapshot = self._require_checkpoint(thread_id, checkpoint_id)
            head = self.execution_head(
                thread_id=thread_id, execution_id=_execution_id(snapshot)
            )
            if _checkpoint_id(head) != checkpoint_id:
                raise ValueError("resume checkpoint must be the current execution head")
            if not _interrupt_kind(snapshot):
                raise ValueError("resume checkpoint has no pending interrupt")
        elif branch_id:
            matches = [
                item
                for item in pending
                if str(item.values.get("branch_id") or "main") == branch_id
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"branch_id={branch_id!r} does not identify exactly one pending execution"
                )
            snapshot = matches[0]
        elif len(pending) == 1:
            snapshot = pending[0]
        elif not pending:
            raise ValueError(f"Thread {thread_id!r} has no pending interrupt")
        else:
            choices = ", ".join(
                f"{item.values.get('branch_id')}:{_checkpoint_id(item)}"
                for item in pending
            )
            raise ValueError(
                "Thread has multiple pending executions; supply checkpoint_id or "
                f"branch_id. Candidates: {choices}"
            )
        self._validate_run_profile(snapshot)
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
        execution_id = _execution_id(snapshot)
        execution_metadata = {
            "execution_id": execution_id,
            "parent_execution_id": _parent_execution_id(snapshot),
            "replayed_from_checkpoint_id": _replayed_from_checkpoint_id(
                snapshot
            ),
            "execution_created_at": _execution_created_at(snapshot).isoformat(),
            "branch_id": str(snapshot.values.get("branch_id") or "main"),
        }
        result = self.graph.invoke(
            Command(resume=resume),
            self._runtime_config(
                snapshot.config,
                metadata=execution_metadata,
            ),
        )
        latest = self.execution_head(
            thread_id=thread_id, execution_id=execution_id
        )
        self._event(
            "checkpoint_created",
            {
                "thread_id": thread_id,
                "checkpoint_id": _checkpoint_id(latest),
                "branch_id": latest.values.get("branch_id"),
                "execution_id": execution_id,
            },
        )
        self._event(
            "run_resumed",
            {
                "thread_id": thread_id,
                "resumed_interrupt_kind": kind,
                "checkpoint_id": _checkpoint_id(latest),
                "execution_id": execution_id,
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
        # Replay creates a new execution in checkpoint metadata while retaining
        # the selected checkpoint's state values. Return the authoritative
        # execution identity so callers do not accidentally select the original
        # execution after resuming a replayed interrupt.
        output = dict(result)
        output.update(
            {
                "execution_id": execution_id,
                "parent_execution_id": _parent_execution_id(latest),
                "replayed_from_checkpoint_id": _replayed_from_checkpoint_id(
                    latest
                ),
                "execution_created_at": _execution_created_at(latest).isoformat(),
            }
        )
        return output

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
        execution_finals: dict[tuple[str, str], str] = {}
        for snapshot in history:
            branch_id = str(snapshot.values.get("branch_id") or root_branch_id)
            key = (branch_id, _execution_id(snapshot))
            if snapshot.values.get("terminal_status") and key not in execution_finals:
                execution_finals[key] = _checkpoint_id(snapshot)
        summaries: list[CheckpointSummary] = []
        for snapshot in history:
            values = snapshot.values
            branch_id = str(values.get("branch_id") or root_branch_id)
            execution_id = _execution_id(snapshot)
            parent_config = snapshot.parent_config or {}
            parent_checkpoint_id = parent_config.get("configurable", {}).get(
                "checkpoint_id"
            )
            metadata = snapshot.metadata or {}
            summaries.append(
                CheckpointSummary(
                    thread_id=thread_id,
                    branch_id=branch_id,
                    execution_id=execution_id,
                    parent_execution_id=_parent_execution_id(snapshot),
                    replayed_from_checkpoint_id=_replayed_from_checkpoint_id(
                        snapshot
                    ),
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
                    final_checkpoint_id=execution_finals.get(
                        (branch_id, execution_id)
                    ),
                )
            )
        return summaries

    def branches(self, *, thread_id: str) -> list[RunBranch]:
        history = self._require_thread(thread_id)
        heads: dict[str, Any] = {}
        finals: dict[str, str] = {}
        created: dict[str, datetime] = {}
        for snapshot in reversed(history):
            if not snapshot.values.get("branch_id"):
                continue
            branch_id = str(snapshot.values["branch_id"])
            branch_created_at = snapshot.values.get("branch_created_at")
            created.setdefault(
                branch_id,
                datetime.fromisoformat(
                    str(branch_created_at).replace("Z", "+00:00")
                )
                if branch_created_at
                else _created_at(snapshot)
            )
        for snapshot in history:
            if not snapshot.values.get("branch_id"):
                continue
            branch_id = str(snapshot.values["branch_id"])
            state_execution_id = str(
                snapshot.values.get("execution_id") or branch_id
            )
            if _execution_id(snapshot) != state_execution_id:
                continue
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
                created_at=created[branch_id],
                terminal_status=snapshot.values.get("terminal_status"),
            )
            for branch_id, snapshot in heads.items()
        ]

    def executions(self, *, thread_id: str) -> list[RunExecution]:
        history = self._require_thread(thread_id)
        heads: dict[str, Any] = {}
        finals: dict[str, str] = {}
        for snapshot in history:
            execution_id = _execution_id(snapshot)
            heads.setdefault(execution_id, snapshot)
            if snapshot.values.get("terminal_status"):
                finals.setdefault(execution_id, _checkpoint_id(snapshot))
        return [
            RunExecution(
                thread_id=thread_id,
                execution_id=execution_id,
                branch_id=str(snapshot.values.get("branch_id") or "main"),
                parent_execution_id=_parent_execution_id(snapshot),
                replayed_from_checkpoint_id=_replayed_from_checkpoint_id(snapshot),
                head_checkpoint_id=_checkpoint_id(snapshot),
                final_checkpoint_id=finals.get(execution_id),
                created_at=_execution_created_at(snapshot),
                terminal_status=snapshot.values.get("terminal_status"),
                interrupt_kind=_interrupt_kind(snapshot),
            )
            for execution_id, snapshot in heads.items()
        ]

    def replay(self, *, thread_id: str, checkpoint_id: str) -> ReplayResult:
        snapshot = self._require_checkpoint(thread_id, checkpoint_id)
        self._validate_run_profile(snapshot)
        if not snapshot.next:
            raise ValueError(
                "Selected checkpoint has no downstream nodes to replay; "
                "choose an earlier checkpoint"
            )
        execution_id = uuid4().hex
        parent_execution_id = _execution_id(snapshot)
        created_at = datetime.now(UTC).isoformat()
        replay_metadata = {
            "execution_id": execution_id,
            "parent_execution_id": parent_execution_id,
            "replayed_from_checkpoint_id": checkpoint_id,
            "execution_created_at": created_at,
            "branch_id": str(snapshot.values.get("branch_id") or "main"),
        }
        self._event(
            "checkpoint_selected",
            {"thread_id": thread_id, "checkpoint_id": checkpoint_id},
        )
        self._event(
            "replay_started",
            {"thread_id": thread_id, "checkpoint_id": checkpoint_id},
        )
        try:
            result = self.graph.invoke(
                None,
                self._runtime_config(snapshot.config, metadata=replay_metadata),
            )
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
        replay_history = [
            item
            for item in self._history(thread_id)
            if _execution_id(item) == execution_id
        ]
        head = replay_history[0] if replay_history else snapshot
        head_checkpoint_id = _checkpoint_id(head)
        final_id = (
            head_checkpoint_id if head.values.get("terminal_status") else None
        )
        self._event(
            "replay_completed",
            {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
                "execution_id": execution_id,
                "result_checkpoint_id": head_checkpoint_id,
            },
        )
        if replay_history:
            self._event(
                "checkpoint_created",
                {
                    "thread_id": thread_id,
                    "checkpoint_id": head_checkpoint_id,
                    "branch_id": head.values.get("branch_id"),
                    "execution_id": execution_id,
                },
            )
        if result.get("__interrupt__"):
            value = result["__interrupt__"][0].value
            self._event(
                "interrupt_requested",
                {
                    "thread_id": thread_id,
                    "checkpoint_id": head_checkpoint_id,
                    "interrupt_kind": (
                        value.get("kind") if isinstance(value, dict) else "unknown"
                    ),
                },
            )
        return ReplayResult(
            thread_id=thread_id,
            branch_id=str(snapshot.values.get("branch_id") or "main"),
            execution_id=execution_id,
            parent_execution_id=parent_execution_id,
            replayed_from_checkpoint_id=checkpoint_id,
            head_checkpoint_id=head_checkpoint_id,
            final_checkpoint_id=final_id,
            terminal_status=head.values.get("terminal_status"),
            interrupt_kind=_interrupt_kind(head),
        )

    @staticmethod
    def _selected_related_taxon(values: dict[str, Any], key: int) -> dict[str, Any]:
        current_key = (values.get("resolved_taxon") or {}).get(
            "accepted_taxon_key"
        )
        if values.get("related_taxon_source_key") != current_key:
            raise ValueError(
                "Saved related candidates do not belong to the current taxon"
            )
        if key == current_key:
            raise ValueError("selected related taxon must differ from the current taxon")
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
        if not changed and selected_taxon is None:
            raise ValueError(
                "Fork updates do not change the selected checkpoint; use replay instead"
            )
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
        execution_id = uuid4().hex
        created_at = datetime.now(UTC).isoformat()
        fork_updates = request.updates.model_dump(mode="json", exclude_none=True)
        update: dict[str, Any] = {
            "expedition_request": updated_request.model_dump(mode="json"),
            "branch_id": branch_id,
            "branch_created_at": created_at,
            "parent_branch_id": str(values.get("branch_id") or "main"),
            "forked_from_checkpoint_id": request.checkpoint_id,
            "fork_updates": fork_updates,
            "fork_created_at": created_at,
            "branch_label": request.branch_label,
            "execution_id": execution_id,
            "parent_execution_id": str(
                values.get("execution_id") or values.get("branch_id") or "main"
            ),
            "replayed_from_checkpoint_id": None,
            "execution_created_at": created_at,
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
            update["low_confidence_accepted"] = False
        if "occurrence" in invalidated:
            update["occurrence_evidence"] = None
        if "weather" in invalidated:
            update["weather_evidence"] = None
        if "public_sites" in invalidated:
            update["public_site_search"] = None
        if selected_taxon:
            previous_taxon = ResolvedTaxon.model_validate(values["resolved_taxon"])
            provenance = previous_taxon.provenance
            if provenance is not None:
                provenance = provenance.model_copy(
                    update={
                        "source_record_type": "accepted_related_taxon_selection",
                        "retrieved_at": datetime.now(UTC),
                        "use_classification": EvidenceUse.validation,
                        "limitations": list(
                            dict.fromkeys(
                                [
                                    *provenance.limitations,
                                    "The selected species came from a bounded GBIF related-taxon query.",
                                    "Taxonomic relation does not imply ecological interchangeability.",
                                ]
                            )
                        ),
                    }
                )
            else:
                provenance = EvidenceItem(
                    source="GBIF Species API",
                    source_record_type="accepted_related_taxon_selection",
                    retrieved_at=datetime.now(UTC),
                    licence=(
                        "GBIF API terms; source datasets retain their own terms"
                    ),
                    attribution="GBIF.org",
                    use_classification=EvidenceUse.validation,
                    limitations=[
                        "Taxonomic relation does not imply ecological interchangeability."
                    ],
                    source_reference="https://www.gbif.org/developer/species",
                )
            update["resolved_taxon"] = ResolvedTaxon(
                status="resolved",
                original_input=updated_request.bird_input,
                normalised_input=selected_taxon["canonical_name"].casefold(),
                provenance=provenance,
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
            update["related_taxon_candidates"] = []
            update["related_taxon_source_key"] = None
        return update

    def fork(self, request: ForkRequest) -> ForkResult:
        snapshot = self._require_checkpoint(request.thread_id, request.checkpoint_id)
        self._validate_run_profile(snapshot)
        values = self._fork_values(snapshot, request)
        values["parent_execution_id"] = _execution_id(snapshot)
        branch_id = values["branch_id"]
        execution_id = values["execution_id"]
        fork_metadata = {
            "execution_id": execution_id,
            "parent_execution_id": values["parent_execution_id"],
            "execution_created_at": values["execution_created_at"],
            "branch_id": branch_id,
        }
        self._event(
            "checkpoint_selected",
            {
                "thread_id": request.thread_id,
                "checkpoint_id": request.checkpoint_id,
            },
        )
        try:
            fork_config = self.graph.update_state(
                self._runtime_config(snapshot.config, metadata=fork_metadata),
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
            self.graph.invoke(
                None,
                self._runtime_config(fork_config, metadata=fork_metadata),
            )
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
            if _execution_id(item) == execution_id
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
                "execution_id": execution_id,
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
            execution_id=execution_id,
            parent_branch_id=values["parent_branch_id"],
            forked_from_checkpoint_id=request.checkpoint_id,
            fork_checkpoint_id=fork_checkpoint_id,
            head_checkpoint_id=_checkpoint_id(head),
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
