"""Deterministic incident classification used by the offline workflow."""

from app.schemas import IncidentClassification


def infer_service_name(description: str) -> str:
    """Infer the service that should be inspected from the incident text."""

    normalized = description.lower()
    known_services = ("checkout", "payment", "orders", "catalog", "auth")
    return next(
        (service for service in known_services if service in normalized),
        "unknown-service",
    )


def classify_incident(description: str) -> IncidentClassification:
    """Return structured severity and routing without an external model."""

    normalized = description.lower()
    service = infer_service_name(description)
    low_risk_signals = ("minor", "slight", "small", "non-critical")
    high_risk_signals = (
        "failure",
        "failed",
        "outage",
        "down",
        "unavailable",
        "error",
        "deployment",
    )

    if any(signal in normalized for signal in low_risk_signals):
        return IncidentClassification(
            service=service,
            severity="low",
            confidence=0.92,
            investigation_route="lightweight",
        )
    if any(signal in normalized for signal in high_risk_signals):
        return IncidentClassification(
            service=service,
            severity="high",
            confidence=0.96,
            investigation_route="full",
        )
    return IncidentClassification(
        service=service,
        severity="low",
        confidence=0.65,
        investigation_route="lightweight",
    )
