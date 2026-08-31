"""Typed expected-failure boundaries for deterministic services."""

from datetime import date

from app.biodiversity.models import (
    ExpeditionRequest,
    LocationStatus,
    SourceFailure,
    TaxonStatus,
    ToolErrorCode,
)
from app.biodiversity.tools import lookup_uk_postcode, resolve_bird_taxon


class FailingPostcodes:
    def lookup(self, normalised_postcode: str):
        raise SourceFailure(
            ToolErrorCode.source_timeout,
            "Postcode source timed out after three bounded attempts.",
            source="Postcodes.io",
            retryable=True,
        )


class FailingTaxonomy:
    def resolve(self, bird_input: str):
        raise SourceFailure(
            ToolErrorCode.source_rate_limit,
            "GBIF rate limited the bounded request.",
            source="GBIF Species API",
            retryable=True,
        )


def test_expected_source_failures_are_typed_not_no_evidence() -> None:
    request = ExpeditionRequest(
        bird_input="Blue tit",
        postcode="SW11 4NJ",
        target_local_date=date(2026, 8, 31),
        duration_hours=1,
    )
    location = lookup_uk_postcode(request, repository=FailingPostcodes())
    taxon = resolve_bird_taxon(request.bird_input, repository=FailingTaxonomy())
    assert location.status == LocationStatus.source_unavailable
    assert "timed out" in location.message
    assert location.tool_error.code == ToolErrorCode.source_timeout
    assert location.tool_error.retryable is True
    assert taxon.status == TaxonStatus.source_unavailable
    assert "rate limited" in taxon.rationale
    assert taxon.tool_error.code == ToolErrorCode.source_rate_limit


class ProgrammingBugTaxonomy:
    def resolve(self, bird_input: str):
        raise AssertionError("unexpected programming bug")


def test_unexpected_programming_errors_surface() -> None:
    try:
        resolve_bird_taxon("Blue tit", repository=ProgrammingBugTaxonomy())
    except AssertionError as exc:
        assert "programming bug" in str(exc)
    else:
        raise AssertionError("unexpected errors must not become insufficient evidence")
