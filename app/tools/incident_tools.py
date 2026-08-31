"""Read-only LangChain tools backed by deterministic local incident data."""

import json
import logging
from collections.abc import Callable
from typing import Any, Literal

from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.types import Command
from pydantic import BaseModel

from app.schemas import (
    DeploymentRecord,
    DeploymentsResult,
    EvidenceItem,
    LogEntry,
    LogsResult,
    MetricLatency,
    MetricsResult,
)

logger = logging.getLogger(__name__)


class ToolDataUnavailable(LookupError):
    """Raised when the offline seed has no data for a requested service."""


CHECKOUT_METRICS = MetricsResult(
    service="checkout",
    time_window="15m",
    error_rate=0.187,
    latency=MetricLatency(p50_ms=420, p95_ms=2450, p99_ms=3810),
    healthy_instances=2,
    total_instances=8,
)

CHECKOUT_LOGS = [
    LogEntry(
        timestamp="2026-08-24T06:56:10Z",
        level="ERROR",
        message="checkout dependency connection refused",
    ),
    LogEntry(
        timestamp="2026-08-24T06:56:13Z",
        level="ERROR",
        message="request failed after 3 retries",
    ),
    LogEntry(
        timestamp="2026-08-24T06:57:02Z",
        level="INFO",
        message="release checkout-v42 is serving production traffic",
    ),
]

CHECKOUT_DEPLOYMENTS = [
    DeploymentRecord(
        version="checkout-v42",
        deployed_at="2026-08-24T06:52:00Z",
        previous_version="checkout-v41",
        commit="8d3f9a1",
    )
]

COLLECTION_TIMES = {
    "metrics": "2026-08-24T07:00:00Z",
    "logs": "2026-08-24T07:00:01Z",
    "deployments": "2026-08-24T07:00:02Z",
}


def _metrics_data(service: str, time_window: str) -> MetricsResult:
    if service != "checkout":
        raise ToolDataUnavailable(f"No seeded metrics for service '{service}'")
    return CHECKOUT_METRICS.model_copy(update={"time_window": time_window})


def _logs_data(service: str, query: str, since: str) -> LogsResult:
    if service != "checkout":
        raise ToolDataUnavailable(f"No seeded logs for service '{service}'")
    return LogsResult(
        service=service,
        since=since,
        query=query,
        entries=CHECKOUT_LOGS,
    )


def _deployments_data(service: str, since: str) -> DeploymentsResult:
    if service != "checkout":
        raise ToolDataUnavailable(f"No seeded deployments for service '{service}'")
    return DeploymentsResult(
        service=service,
        since=since,
        deployments=CHECKOUT_DEPLOYMENTS,
    )


def _tool_command(
    *,
    source: Literal["metrics", "logs", "deployments"],
    tool_name: str,
    runtime: ToolRuntime,
    read: Callable[[], BaseModel],
) -> Command:
    """Return evidence plus the ToolMessage required by the model protocol."""

    if runtime.tool_call_id is None:
        raise RuntimeError(f"{tool_name} must be executed by ToolNode")

    status: Literal["success", "error"] = "success"
    errors: list[str] = []
    try:
        raw: dict[str, Any] = read().model_dump(mode="json")
    except ToolDataUnavailable as exc:
        status = "error"
        error = str(exc)
        errors.append(error)
        raw = {
            "error": error,
            "error_type": type(exc).__name__,
        }
        logger.warning("TOOL %s unavailable: %s", tool_name, error)

    evidence = EvidenceItem(
        source=source,
        tool_name=tool_name,
        collected_at=COLLECTION_TIMES[source],
        raw=raw,
    )
    update: dict[str, Any] = {
        "evidence": [evidence],
        "executed_tools": [tool_name],
        "visited_nodes": ["tools"],
        "messages": [
            ToolMessage(
                content=json.dumps(raw, ensure_ascii=False),
                tool_call_id=runtime.tool_call_id,
                name=tool_name,
                status=status,
            )
        ],
    }
    if errors:
        update["tool_errors"] = errors
    return Command(update=update)


@tool
def query_service_metrics(
    service: str,
    time_window: str,
    runtime: ToolRuntime,
) -> Command:
    """Read error-rate and latency metrics for a service and time window."""

    logger.info(
        "TOOL query_service_metrics called service=%s time_window=%s",
        service,
        time_window,
    )
    return _tool_command(
        source="metrics",
        tool_name="query_service_metrics",
        runtime=runtime,
        read=lambda: _metrics_data(service, time_window),
    )


@tool
def search_service_logs(
    service: str,
    query: str,
    since: str,
    runtime: ToolRuntime,
) -> Command:
    """Search structured read-only service logs since an ISO-8601 timestamp."""

    logger.info(
        "TOOL search_service_logs called service=%s since=%s query=%r",
        service,
        since,
        query,
    )
    return _tool_command(
        source="logs",
        tool_name="search_service_logs",
        runtime=runtime,
        read=lambda: _logs_data(service, query, since),
    )


@tool
def list_recent_deployments(
    service: str,
    since: str,
    runtime: ToolRuntime,
) -> Command:
    """List read-only deployment records since an ISO-8601 timestamp."""

    logger.info(
        "TOOL list_recent_deployments called service=%s since=%s", service, since
    )
    return _tool_command(
        source="deployments",
        tool_name="list_recent_deployments",
        runtime=runtime,
        read=lambda: _deployments_data(service, since),
    )


INVESTIGATION_TOOLS = [
    query_service_metrics,
    search_service_logs,
    list_recent_deployments,
]
