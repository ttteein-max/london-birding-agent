"""Structured domain schemas shared by the incident workflow and tools."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IncidentClassification(BaseModel):
    """Deterministic classification used to select an investigation route."""

    model_config = ConfigDict(extra="forbid")

    service: str = Field(min_length=1)
    severity: Literal["low", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    investigation_route: Literal["lightweight", "full"]


class EvidenceItem(BaseModel):
    """Raw output captured from one read-only investigation tool."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["metrics", "logs", "deployments"]
    tool_name: str = Field(min_length=1)
    collected_at: str = Field(min_length=1)
    raw: dict[str, Any]


class ProposedAction(BaseModel):
    """A structured, reviewable recovery action proposal."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1)
    arguments: dict[str, str]
    risk_level: Literal["low", "medium", "high"]
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rollback_arguments(self) -> "ProposedAction":
        """Require an explicit service and target version for rollbacks."""

        if self.tool_name == "rollback_deployment":
            required = {"service", "target_version"}
            missing = required.difference(self.arguments)
            if missing or any(not self.arguments[key] for key in required):
                raise ValueError(
                    "rollback_deployment requires non-empty service and target_version"
                )
        return self


class FinalInvestigation(BaseModel):
    """Validated structured output requested from the finalizer model."""

    model_config = ConfigDict(extra="forbid")

    diagnosis: str = Field(min_length=1)
    diagnosis_confidence: float = Field(ge=0.0, le=1.0)
    investigation_status: Literal[
        "complete", "insufficient_evidence", "step_limit_reached"
    ]
    proposed_action: ProposedAction


class MetricLatency(BaseModel):
    """Latency percentiles in milliseconds."""

    model_config = ConfigDict(extra="forbid")

    p50_ms: int = Field(ge=0)
    p95_ms: int = Field(ge=0)
    p99_ms: int = Field(ge=0)


class MetricsResult(BaseModel):
    """Structured output returned by the metrics tool."""

    model_config = ConfigDict(extra="forbid")

    service: str
    time_window: str
    error_rate: float = Field(ge=0.0, le=1.0)
    latency: MetricLatency
    healthy_instances: int = Field(ge=0)
    total_instances: int = Field(ge=1)


class LogEntry(BaseModel):
    """One structured service log entry."""

    model_config = ConfigDict(extra="forbid")

    timestamp: str
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"]
    message: str


class LogsResult(BaseModel):
    """Structured output returned by the log search tool."""

    model_config = ConfigDict(extra="forbid")

    service: str
    since: str
    query: str
    entries: list[LogEntry]


class DeploymentRecord(BaseModel):
    """One structured deployment record."""

    model_config = ConfigDict(extra="forbid")

    version: str
    deployed_at: str
    previous_version: str
    commit: str


class DeploymentsResult(BaseModel):
    """Structured output returned by the deployment lookup tool."""

    model_config = ConfigDict(extra="forbid")

    service: str
    since: str
    deployments: list[DeploymentRecord]
