"""Conditional routing for the incident-response graph."""

import logging
from typing import Literal

from app.graph.state import IncidentState

logger = logging.getLogger(__name__)


def route_incident(
    state: IncidentState,
) -> Literal["summarize_low_risk", "investigator_agent"]:
    """Route only on the explicit investigation route, not severity."""

    route = state["classification"].investigation_route
    if route == "full":
        logger.info("ROUTE full -> investigator_agent")
        return "investigator_agent"

    logger.info("ROUTE lightweight -> summarize_low_risk")
    return "summarize_low_risk"
