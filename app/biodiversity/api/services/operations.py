"""Application orchestration for asynchronous graph mutations and safe reads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.biodiversity.api.dependencies import APISettings
from app.biodiversity.api.repositories import RunCatalog
from app.biodiversity.api.schemas import (
    ComparisonView,
    FinalPlanView,
    ForkRunRequest,
    HistoryView,
    OperationAccepted,
    OperationKind,
    OperationView,
    PendingDecisionView,
    RouteGeometryView,
    RouteOptionsView,
    ResumeRunRequest,
    RunDetail,
    RunSummary,
    SubmittedRequestView,
    WorkflowTopologyView,
)
from app.biodiversity.api.services.public_demo import DemoRetention, PublicDemoGuard
from app.biodiversity.api.services.topology import build_workflow_topology
from app.biodiversity.api.services.views import SafeCheckpointViews
from app.biodiversity.graph import build_biodiversity_graph
from app.biodiversity.model_factory import create_live_chat_model
from app.biodiversity.observability import (
    AgentRunEvent,
    AgentRunEventBatch,
    AgentRunRecorder,
    InMemoryAgentEventBroker,
)
from app.biodiversity.orchestration import BackendDependencies
from app.biodiversity.reporting import save_biodiversity_run_report
from app.biodiversity.routing import (
    FileRouteGeometryStore,
    RoutingServices,
    TFL_JOURNEY_ENDPOINT,
)
from app.biodiversity.run_models import ForkRequest, RunProfile
from app.biodiversity.runs import BiodiversityRunManager
from app.biodiversity.testing import make_scripted_biodiversity_models


TERMINAL_OPERATION_STATUSES = {"waiting_for_input", "completed", "failed"}


def _profile(
    data_mode: str,
    model_mode: str,
    *,
    expected_model_identifier: str | None = None,
) -> RunProfile:
    if model_mode == "scripted":
        model_identifier = "scripted-biodiversity-v1"
        endpoint_fingerprint = "local-scripted"
    else:
        model_identifier = os.getenv("OPENAI_MODEL")
        if not model_identifier or not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("Live model runtime is not configured")
        if expected_model_identifier and model_identifier != expected_model_identifier:
            raise ValueError("Configured model does not match the saved run manifest")
        endpoint = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        endpoint_fingerprint = hashlib.sha256(endpoint.encode()).hexdigest()[:16]
    routing_provider = (
        "fixture-openrouteservice"
        if data_mode == "fixture"
        else "tfl-journey-planner"
    )
    routing_provider_version = (
        "ors-api-shaped-2026-09-04"
        if data_mode == "fixture"
        else "unified-api-v1-least-time-v2"
    )
    routing_endpoint = (
        "local-fixture"
        if data_mode == "fixture"
        else TFL_JOURNEY_ENDPOINT
    )
    return RunProfile(
        data_mode=data_mode,
        model_mode=model_mode,
        model_identifier=model_identifier,
        endpoint_fingerprint=endpoint_fingerprint,
        routing_provider=routing_provider,
        routing_provider_version=routing_provider_version,
        routing_profile=(
            "foot-walking"
            if data_mode == "fixture"
            else "public-transport-and-walking"
        ),
        routing_endpoint_fingerprint=(
            "local-fixture"
            if data_mode == "fixture"
            else hashlib.sha256(routing_endpoint.encode()).hexdigest()[:16]
        ),
    )


class _OperationEventSink:
    def __init__(
        self,
        broker: InMemoryAgentEventBroker,
        catalog: RunCatalog,
    ) -> None:
        self.broker = broker
        self.catalog = catalog

    def publish(self, event: AgentRunEvent) -> None:
        self.broker.publish(event)
        self.catalog.update_operation(
            event.run_id,
            last_event_sequence=event.sequence,
        )


class GraphRuntime:
    """Build managers around one lifespan-owned durable checkpointer."""

    def __init__(self, checkpointer: Any, *, route_runtime: Path) -> None:
        self.checkpointer = checkpointer
        self.route_runtime = route_runtime
        self._reader_graph = self._build_graph(
            data_mode="fixture",
            model_mode="scripted",
        )

    def _build_graph(
        self,
        *,
        data_mode: str,
        model_mode: str,
        recorder: AgentRunRecorder | None = None,
    ) -> Any:
        dependencies = (
            BackendDependencies.fixture()
            if data_mode == "fixture"
            else BackendDependencies.live()
        )
        event_callback = (
            (
                lambda event_type, payload: recorder.record_event(
                    event_type, payload=payload
                )
            )
            if recorder is not None
            else None
        )
        route_runtime = self.route_runtime
        routing_services = (
            RoutingServices.fixture(
                runtime_directory=route_runtime,
                event_callback=event_callback,
            )
            if data_mode == "fixture"
            else RoutingServices.live(
                runtime_directory=route_runtime,
                event_callback=event_callback,
            )
        )
        if model_mode == "scripted":
            parser, evidence, composer = make_scripted_biodiversity_models()
            return build_biodiversity_graph(
                parser_model=parser,
                evidence_model=evidence,
                composer_model=composer,
                dependencies=dependencies,
                routing_services=routing_services,
                checkpointer=self.checkpointer,
            )
        return build_biodiversity_graph(
            create_live_chat_model(),
            dependencies=dependencies,
            routing_services=routing_services,
            checkpointer=self.checkpointer,
        )

    def reader(self) -> BiodiversityRunManager:
        return BiodiversityRunManager(self._reader_graph)

    def topology(self) -> WorkflowTopologyView:
        return build_workflow_topology(self._reader_graph)

    def manager(
        self,
        profile: RunProfile,
        *,
        recorder: AgentRunRecorder,
    ) -> BiodiversityRunManager:
        # Scripted models intentionally keep small invocation counters for tests.
        # A fresh graph/model set per mutation prevents those counters from being
        # shared by different concurrent threads. The durable checkpointer remains
        # the single shared state boundary.
        graph = self._build_graph(
            data_mode=profile.data_mode,
            model_mode=profile.model_mode,
            recorder=recorder,
        )
        return BiodiversityRunManager(
            graph,
            recorder=recorder,
            run_profile=profile,
        )

    def profile_for_thread(self, thread_id: str) -> RunProfile:
        manifest = self.reader().manifest(thread_id=thread_id)
        if manifest is None:
            raise ValueError("Legacy threads without a run manifest are not API-mutable")
        if manifest.workflow_version != "phase-5.0":
            raise ValueError(
                "Phase 4 executions are read-only; create a new Phase 5 run."
            )
        return _profile(
            manifest.data_mode,
            manifest.model_mode,
            expected_model_identifier=manifest.model_identifier,
        )

    def thread_exists(self, thread_id: str) -> bool:
        try:
            self.reader().history(thread_id=thread_id)
        except ValueError as exc:
            if "No biodiversity run exists" in str(exc):
                return False
            raise
        return True

    def delete_thread(self, thread_id: str) -> None:
        self.checkpointer.delete_thread(thread_id)


class OperationEngine:
    """Synchronous worker body; callers always dispatch it with ``to_thread``."""

    def __init__(
        self,
        *,
        runtime: GraphRuntime,
        catalog: RunCatalog,
        broker: InMemoryAgentEventBroker,
        settings: APISettings,
    ) -> None:
        self.runtime = runtime
        self.catalog = catalog
        self.broker = broker
        self.settings = settings

    @staticmethod
    def _failure_code(exc: Exception) -> str:
        if isinstance(exc, ValueError):
            return "invalid_operation"
        if isinstance(exc, RuntimeError):
            return "upstream_unavailable"
        return "internal_error"

    def execute(
        self,
        *,
        operation_id: str,
        thread_id: str,
        kind: OperationKind,
        payload: dict[str, Any],
        profile: RunProfile,
    ) -> None:
        self.catalog.update_operation(
            operation_id,
            status="running",
            started_at=datetime.now(UTC),
        )
        recorder = AgentRunRecorder(
            run_id=operation_id,
            thread_id=thread_id,
            event_sink=_OperationEventSink(self.broker, self.catalog),
        )
        snapshot: Any | None = None
        try:
            manager = self.runtime.manager(profile, recorder=recorder)
            if kind == "start":
                result = manager.start(payload["request"], thread_id=thread_id)
                snapshot = manager.execution_head(
                    thread_id=thread_id,
                    execution_id=result["execution_id"],
                )
            elif kind == "resume":
                result = manager.resume(
                    thread_id=thread_id,
                    resume=payload["resume"],
                    checkpoint_id=payload.get("checkpoint_id"),
                    branch_id=payload.get("branch_id"),
                )
                snapshot = manager.execution_head(
                    thread_id=thread_id,
                    execution_id=result["execution_id"],
                )
            elif kind == "replay":
                replay = manager.replay(
                    thread_id=thread_id,
                    checkpoint_id=payload["checkpoint_id"],
                )
                snapshot = manager.snapshot(
                    thread_id=thread_id,
                    checkpoint_id=replay.head_checkpoint_id,
                )
            else:
                fork = manager.fork(
                    ForkRequest(
                        thread_id=thread_id,
                        checkpoint_id=payload["checkpoint_id"],
                        updates=payload["updates"],
                        branch_label=payload.get("branch_label"),
                    )
                )
                snapshot = manager.snapshot(
                    thread_id=thread_id,
                    checkpoint_id=fork.head_checkpoint_id,
                )
            checkpoint_id = str(snapshot.config["configurable"]["checkpoint_id"])
            values = dict(snapshot.values)
            metadata = dict(snapshot.metadata or {})
            interrupt_kind = next(
                (
                    str(item.value.get("kind"))
                    for task in snapshot.tasks
                    for item in task.interrupts
                    if isinstance(item.value, dict) and item.value.get("kind")
                ),
                None,
            )
            if interrupt_kind == "route_tradeoff":
                recorder.record_event(
                    "route_hitl_requested",
                    payload={
                        "status": "waiting_for_input",
                        "checkpoint_id": checkpoint_id,
                    },
                )
            walking_plan = dict(values.get("validated_walking_plan") or {})
            if walking_plan:
                recorder.record_event(
                    "route_finalised",
                    payload={
                        "status": str(walking_plan.get("status") or "unknown"),
                        "provider": walking_plan.get("provider") or "not_called",
                        "cache_status": walking_plan.get("cache_status") or "bypassed",
                        "route_id": next(
                            (
                                str(item.get("option_id"))
                                for item in values.get("route_options") or []
                                if item.get("site_id")
                                == walking_plan.get("selected_site_id")
                            ),
                            "no-selected-route",
                        ),
                    },
                )
            status = "waiting_for_input" if interrupt_kind else "completed"
            recorder.finish(
                "completed",
                payload={"terminal_status": values.get("terminal_status")},
            )
            report_reference = operation_id
            report_directory = self.settings.report_root / report_reference
            save_biodiversity_run_report(
                report_directory,
                recorder=recorder,
                result=values,
                request=None,
                data_mode=profile.data_mode,
                model_mode=profile.model_mode,
                checkpoint_id=checkpoint_id,
                execution_id=str(
                    metadata.get("execution_id")
                    or values.get("execution_id")
                    or "main"
                ),
                parent_execution_id=metadata.get("parent_execution_id")
                or values.get("parent_execution_id"),
                replayed_from_checkpoint_id=metadata.get(
                    "replayed_from_checkpoint_id"
                )
                or values.get("replayed_from_checkpoint_id"),
                run_manifest=values.get("run_manifest"),
            )
            self.catalog.update_operation(
                operation_id,
                status=status,
                branch_id=str(values.get("branch_id") or "main"),
                execution_id=str(
                    metadata.get("execution_id")
                    or values.get("execution_id")
                    or "main"
                ),
                finished_at=datetime.now(UTC),
                current_checkpoint_id=checkpoint_id,
                report_directory=report_reference,
                interrupt_kind=interrupt_kind,
            )
        except Exception as exc:
            recorder.finish("failed", payload={"error_type": type(exc).__name__})
            report_reference = operation_id
            try:
                save_biodiversity_run_report(
                    self.settings.report_root / report_reference,
                    recorder=recorder,
                    result=dict(snapshot.values) if snapshot is not None else {},
                    request=None,
                    data_mode=profile.data_mode,
                    model_mode=profile.model_mode,
                    checkpoint_id=(
                        str(snapshot.config["configurable"]["checkpoint_id"])
                        if snapshot is not None
                        else None
                    ),
                )
            except Exception:
                report_reference = ""
            self.catalog.update_operation(
                operation_id,
                status="failed",
                finished_at=datetime.now(UTC),
                report_directory=report_reference or None,
                error_code=self._failure_code(exc),
            )


class OperationCoordinator:
    """Reject same-thread overlap while allowing different threads to run."""

    def __init__(
        self,
        *,
        engine: OperationEngine,
        catalog: RunCatalog,
        settings: APISettings,
    ) -> None:
        self.engine = engine
        self.catalog = catalog
        self.settings = settings
        self._guard = asyncio.Lock()
        self._active_threads: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._demo_guard = PublicDemoGuard(settings, catalog)

    async def submit(
        self,
        *,
        thread_id: str,
        kind: OperationKind,
        payload: dict[str, Any],
        profile: RunProfile,
    ) -> OperationAccepted:
        async with self._guard:
            if thread_id in self._active_threads or self.catalog.has_active_mutation(
                thread_id
            ):
                raise RuntimeError("same_thread_mutation_conflict")
            if kind == "start" and self.catalog.thread_exists(thread_id):
                raise RuntimeError("thread_already_exists")
            if len(self._active_threads) >= self.settings.max_concurrent_operations:
                raise RuntimeError("operation_capacity")
            self._demo_guard.admit(kind=kind)
            operation_id = uuid4().hex
            self.catalog.create_operation(
                operation_id=operation_id,
                thread_id=thread_id,
                kind=kind,
                data_mode=profile.data_mode,
                model_mode=profile.model_mode,
            )
            self._active_threads.add(thread_id)
            task = asyncio.create_task(
                self._run(
                    operation_id=operation_id,
                    thread_id=thread_id,
                    kind=kind,
                    payload=payload,
                    profile=profile,
                )
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return OperationAccepted(
            operation_id=operation_id,
            thread_id=thread_id,
            events_url=f"/api/v1/operations/{operation_id}/events",
        )

    async def _run(
        self,
        *,
        operation_id: str,
        thread_id: str,
        kind: OperationKind,
        payload: dict[str, Any],
        profile: RunProfile,
    ) -> None:
        try:
            if self.settings.operation_start_delay_seconds:
                await asyncio.sleep(self.settings.operation_start_delay_seconds)
            await asyncio.to_thread(
                self.engine.execute,
                operation_id=operation_id,
                thread_id=thread_id,
                kind=kind,
                payload=payload,
                profile=profile,
            )
        finally:
            async with self._guard:
                self._active_threads.discard(thread_id)

    async def close(self) -> None:
        tasks = list(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class Phase4Application:
    def __init__(
        self,
        *,
        settings: APISettings,
        catalog: RunCatalog,
        checkpointer: Any,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.broker = InMemoryAgentEventBroker()
        self.runtime = GraphRuntime(
            checkpointer,
            route_runtime=settings.route_runtime,
        )
        self.views = SafeCheckpointViews(
            settings.osm_directory,
            expose_request_details=not settings.public_demo,
        )
        self.engine = OperationEngine(
            runtime=self.runtime,
            catalog=catalog,
            broker=self.broker,
            settings=settings,
        )
        self.coordinator = OperationCoordinator(
            engine=self.engine,
            catalog=catalog,
            settings=settings,
        )
        self.retention = DemoRetention(
            settings=settings,
            catalog=catalog,
            runtime=self.runtime,
        )
        self._housekeeping_task: asyncio.Task[None] | None = None

    def _ensure_mode_allowed(self, data_mode: str, model_mode: str) -> None:
        if (data_mode, model_mode) not in self.settings.allowed_run_modes:
            raise PermissionError("run_mode_not_allowed")

    def start_profile(self, data_mode: str, model_mode: str) -> RunProfile:
        self._ensure_mode_allowed(data_mode, model_mode)
        return _profile(data_mode, model_mode)

    def thread_profile(self, thread_id: str) -> RunProfile:
        profile = self.runtime.profile_for_thread(thread_id)
        self._ensure_mode_allowed(profile.data_mode, profile.model_mode)
        return profile

    async def start(self) -> None:
        await asyncio.to_thread(self.retention.cleanup)
        if self.settings.public_demo and self.settings.run_retention_seconds:
            self._housekeeping_task = asyncio.create_task(self._housekeeping_loop())

    async def _housekeeping_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.cleanup_interval_seconds)
            await asyncio.to_thread(self.retention.cleanup)

    def thread_exists(self, thread_id: str) -> bool:
        return self.catalog.thread_exists(thread_id) or self.runtime.thread_exists(thread_id)

    def validate_replay(self, *, thread_id: str, checkpoint_id: str) -> None:
        snapshot = self.runtime.reader().snapshot(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )
        if not snapshot.next:
            raise ValueError("terminal_checkpoint")

    def validate_fork(self, *, thread_id: str, request: ForkRunRequest) -> None:
        manager = self.runtime.reader()
        snapshot = manager.snapshot(
            thread_id=thread_id,
            checkpoint_id=request.checkpoint_id,
        )
        BiodiversityRunManager._fork_values(
            snapshot,
            ForkRequest(
                thread_id=thread_id,
                checkpoint_id=request.checkpoint_id,
                updates=request.updates,
                branch_label=request.branch_label,
            ),
        )

    def validate_resume(
        self,
        *,
        thread_id: str,
        request: ResumeRunRequest,
    ) -> PendingDecisionView:
        pending = self.views.pending_decisions(
            self.runtime.reader(),
            thread_id=thread_id,
        )
        matches = pending
        if request.checkpoint_id:
            matches = [item for item in pending if item.checkpoint_id == request.checkpoint_id]
        elif request.branch_id:
            matches = [item for item in pending if item.branch_id == request.branch_id]
        if len(matches) > 1:
            raise RuntimeError("ambiguous_resume")
        if not matches:
            raise ValueError("missing_pending_interrupt")
        if matches[0].kind != request.decision.kind:
            raise ValueError("resume_kind_mismatch")
        self.runtime.reader().validate_resume(
            thread_id=thread_id,
            resume=request.manager_payload(),
            checkpoint_id=request.checkpoint_id,
            branch_id=request.branch_id,
        )
        return matches[0]

    def operation(self, operation_id: str) -> OperationView | None:
        return self.catalog.operation(operation_id)

    def list_runs(self) -> list[RunSummary]:
        grouped: dict[str, list[OperationView]] = {}
        for operation in self.catalog.list_operations():
            grouped.setdefault(operation.thread_id, []).append(operation)
        return [
            self._run_summary(operations[0]).model_copy(
                update={"created_at": operations[-1].created_at}
            )
            for operations in grouped.values()
        ]

    def workflow_topology(self) -> WorkflowTopologyView:
        return self.runtime.topology()

    @staticmethod
    def _run_summary(operation: OperationView) -> RunSummary:
        return RunSummary(
            thread_id=operation.thread_id,
            status=operation.status,
            data_mode=operation.data_mode,
            model_mode=operation.model_mode,
            created_at=operation.created_at,
            updated_at=operation.finished_at
            or operation.started_at
            or operation.created_at,
            current_checkpoint_id=operation.current_checkpoint_id,
            branch_id=operation.branch_id,
            execution_id=operation.execution_id,
            interrupt_kind=operation.interrupt_kind,
        )

    def run_detail(self, thread_id: str) -> RunDetail | None:
        operations = self.catalog.operations_for_thread(thread_id)
        if not operations:
            return None
        latest = operations[0]
        current = next(
            (item for item in operations if item.current_checkpoint_id),
            latest,
        )
        summary = self._run_summary(latest).model_copy(
            update={
                "created_at": operations[-1].created_at,
                "current_checkpoint_id": current.current_checkpoint_id,
                "branch_id": current.branch_id,
                "execution_id": current.execution_id,
            }
        )
        branches = []
        executions = []
        pending: list[PendingDecisionView] = []
        final_plan: FinalPlanView | None = None
        submitted_request: SubmittedRequestView | None = None
        manager: BiodiversityRunManager | None = None
        if current.current_checkpoint_id:
            manager = self.runtime.reader()
            branches = manager.branches(thread_id=thread_id)
            executions = manager.executions(thread_id=thread_id)
            pending = self.views.pending_decisions(manager, thread_id=thread_id)
            snapshot = manager.snapshot(
                thread_id=thread_id,
                checkpoint_id=current.current_checkpoint_id,
            )
            if not self.settings.public_demo:
                request_text = snapshot.values.get("original_request_text")
                if isinstance(request_text, str) and request_text.strip():
                    submitted_request = SubmittedRequestView(text=request_text)
            final_plan = self.views.final_plan(snapshot)
        if not self.settings.public_demo and submitted_request is None:
            manager = manager or self.runtime.reader()
            try:
                request_history = manager.history(thread_id=thread_id)
                request_snapshot = manager.snapshot(
                    thread_id=thread_id,
                    checkpoint_id=request_history[0].checkpoint_id,
                )
            except (IndexError, ValueError):
                pass
            else:
                request_text = request_snapshot.values.get("original_request_text")
                if isinstance(request_text, str) and request_text.strip():
                    submitted_request = SubmittedRequestView(text=request_text)
        return RunDetail(
            run=summary,
            submitted_request=submitted_request,
            operations=operations,
            branches=branches,
            executions=executions,
            pending_decisions=pending,
            final_plan=final_plan,
        )

    def history(self, thread_id: str) -> HistoryView:
        manager = self.runtime.reader()
        return HistoryView(
            checkpoints=manager.history(thread_id=thread_id),
            branches=manager.branches(thread_id=thread_id),
            executions=manager.executions(thread_id=thread_id),
        )

    def route_options(
        self,
        *,
        thread_id: str,
        checkpoint_id: str,
    ) -> RouteOptionsView:
        return self.views.route_options(
            self.runtime.reader(),
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )

    def route_geometry(
        self,
        *,
        thread_id: str,
        checkpoint_id: str,
        route_geometry_reference: str,
    ) -> RouteGeometryView:
        manager = self.runtime.reader()
        if self.settings.public_demo:
            manifest = manager.manifest(thread_id=thread_id)
            if manifest is None or manifest.data_mode != "fixture":
                raise PermissionError("public_route_geometry_requires_fixture")
        return self.views.route_geometry(
            manager,
            FileRouteGeometryStore(self.settings.route_runtime / "geometries"),
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            route_geometry_reference=route_geometry_reference,
            public_demo=self.settings.public_demo,
        )

    def compare(
        self,
        *,
        thread_id: str,
        checkpoint_a: str,
        checkpoint_b: str,
    ) -> ComparisonView:
        comparison = self.runtime.reader().compare(
            thread_id=thread_id,
            checkpoint_a=checkpoint_a,
            checkpoint_b=checkpoint_b,
        )
        return ComparisonView(comparison=comparison)

    def _report_events(self, operation_id: str) -> list[AgentRunEvent]:
        reference = self.catalog.report_directory(operation_id)
        if not reference:
            return []
        root = self.settings.report_root.resolve()
        directory = (root / reference).resolve()
        try:
            directory.relative_to(root)
        except ValueError:
            return []
        path = directory / "events.json"
        if not path.is_file():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [AgentRunEvent.model_validate(item) for item in payload.get("events", [])]

    def event_batch(
        self,
        *,
        operation_id: str,
        after_sequence: int,
        wait: bool,
    ) -> AgentRunEventBatch:
        operation = self.catalog.operation(operation_id)
        if operation is None:
            raise KeyError(operation_id)
        if after_sequence > operation.last_event_sequence:
            raise ValueError("after_sequence_newer_than_latest")
        try:
            live = (
                self.broker.wait_for_events(
                    operation_id,
                    after_sequence=after_sequence,
                    timeout_seconds=self.settings.sse_heartbeat_seconds,
                )
                if wait and operation.status not in TERMINAL_OPERATION_STATUSES
                else self.broker.events_after(
                    operation_id,
                    after_sequence=after_sequence,
                )
            )
        except ValueError:
            live = AgentRunEventBatch(
                run_id=operation_id,
                after_sequence=after_sequence,
                latest_sequence=0,
                terminal=False,
            )
        if live.events or live.terminal:
            return live
        operation = self.catalog.operation(operation_id) or operation
        persisted = self._report_events(operation_id)
        if persisted:
            latest = persisted[-1].sequence
            return AgentRunEventBatch(
                run_id=operation_id,
                after_sequence=after_sequence,
                latest_sequence=latest,
                terminal=operation.status in TERMINAL_OPERATION_STATUSES,
                events=[item for item in persisted if item.sequence > after_sequence],
            )
        return AgentRunEventBatch(
            run_id=operation_id,
            after_sequence=after_sequence,
            latest_sequence=operation.last_event_sequence,
            terminal=operation.status in TERMINAL_OPERATION_STATUSES,
        )

    async def close(self) -> None:
        if self._housekeeping_task is not None:
            self._housekeeping_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._housekeeping_task
        await self.coordinator.close()
