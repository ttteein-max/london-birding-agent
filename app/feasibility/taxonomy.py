"""Reusable GBIF-backed resolver for arbitrary bird-name input."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

JsonFetcher = Callable[[str, dict[str, Any] | None], tuple[Any, str]]


def normalise_name(value: str) -> str:
    """Normalise user-facing name text without changing the preserved input."""

    return " ".join(value.casefold().strip().split())


def _name_tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z]+", normalise_name(value)))


class TaxonCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched_common_name: str | None = None
    scientific_name: str
    canonical_name: str
    accepted_taxon_key: int
    rank: str
    taxonomic_status: str
    class_name: str | None = None
    order: str | None = None
    family: str | None = None
    genus: str | None = None
    search_method: str
    match_confidence: int | None = None


class TaxonomyOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["resolved", "human_selection_required", "taxon_not_found"]
    original_input: str
    normalised_input: str
    matched_common_name: str | None = None
    scientific_name: str | None = None
    canonical_name: str | None = None
    accepted_taxon_key: int | None = None
    rank: str | None = None
    taxonomic_status: str | None = None
    class_name: str | None = None
    order: str | None = None
    family: str | None = None
    genus: str | None = None
    match_method: str | None = None
    match_confidence: int | None = None
    rationale: str = Field(min_length=1)
    candidates: list[TaxonCandidate] = Field(default_factory=list)
    request_urls: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_outcome_fields(self) -> "TaxonomyOutcome":
        resolved_fields = (
            self.scientific_name,
            self.canonical_name,
            self.accepted_taxon_key,
            self.rank,
            self.taxonomic_status,
            self.match_method,
        )
        if self.outcome == "resolved" and any(value is None for value in resolved_fields):
            raise ValueError("resolved outcomes require accepted taxon fields")
        if self.outcome == "human_selection_required" and len(self.candidates) < 2:
            raise ValueError("ambiguous outcomes require at least two candidates")
        return self


def _is_accepted_bird_species(record: dict[str, Any]) -> bool:
    status = record.get("taxonomicStatus") or record.get("status")
    return (
        record.get("rank") == "SPECIES"
        and record.get("class") == "Aves"
        and status == "ACCEPTED"
        and not record.get("synonym", False)
    )


def _english_common_names(record: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in record.get("vernacularNames") or []:
        value = item.get("vernacularName")
        language = item.get("language")
        if value and language in (None, "", "eng") and value not in names:
            names.append(value)
    return names


def _candidate(record: dict[str, Any], common_name: str | None, method: str) -> TaxonCandidate:
    return TaxonCandidate(
        matched_common_name=common_name,
        scientific_name=record.get("accepted") or record["scientificName"],
        canonical_name=record.get("species") or record.get("canonicalName") or record["scientificName"],
        accepted_taxon_key=record.get("acceptedKey") or record.get("speciesKey") or record.get("key") or record["usageKey"],
        rank=record["rank"],
        taxonomic_status=record.get("taxonomicStatus") or record.get("status"),
        class_name=record.get("class"),
        order=record.get("order"),
        family=record.get("family"),
        genus=record.get("genus"),
        search_method=method,
        match_confidence=record.get("confidence"),
    )


class GBIFBirdNameResolver:
    """Resolve scientific and English common names without a species whitelist."""

    def __init__(self, fetch_json: JsonFetcher) -> None:
        self._fetch_json = fetch_json

    def resolve(self, user_input: str) -> TaxonomyOutcome:
        original = user_input
        query = normalise_name(user_input)
        if not query:
            return TaxonomyOutcome(
                outcome="taxon_not_found",
                original_input=original,
                normalised_input=query,
                rationale="The bird name is empty after whitespace normalisation.",
            )

        direct, direct_url = self._fetch_json(
            "https://api.gbif.org/v1/species/match",
            {"name": query, "kingdom": "Animalia"},
        )
        request_urls = [direct_url]
        if _is_accepted_bird_species(direct) and direct.get("matchType") == "EXACT":
            candidate = _candidate(direct, None, "gbif_species_match")
            return self._resolved(
                original,
                query,
                candidate,
                "GBIF produced an exact accepted Aves species match from the user text.",
                request_urls,
            )

        search, search_url = self._fetch_json(
            "https://api.gbif.org/v1/species/search",
            {
                "q": query,
                "rank": "SPECIES",
                "highertaxon_key": 212,
                "status": "ACCEPTED",
                "limit": 100,
            },
        )
        request_urls.append(search_url)
        exact: dict[int, TaxonCandidate] = {}
        partial: dict[int, TaxonCandidate] = {}
        query_tokens = _name_tokens(query)
        for record in search.get("results", []):
            if not _is_accepted_bird_species(record):
                continue
            names = _english_common_names(record)
            canonical = normalise_name(record.get("canonicalName") or record.get("species") or "")
            scientific = normalise_name(record.get("scientificName") or "")
            exact_common = next((name for name in names if normalise_name(name) == query), None)
            key = record.get("acceptedKey") or record.get("speciesKey") or record.get("key")
            if key is None:
                continue
            if query in (canonical, scientific) or scientific.startswith(f"{query} "):
                exact[key] = _candidate(record, exact_common, "gbif_species_search_scientific")
                continue
            if exact_common:
                exact[key] = _candidate(record, exact_common, "gbif_species_search_common_name")
                continue
            related_name = next(
                (
                    name
                    for name in names
                    if query_tokens
                    and query_tokens.issubset(_name_tokens(name))
                ),
                None,
            )
            if related_name:
                partial[key] = _candidate(record, related_name, "gbif_species_search_related_common_name")

        if len(exact) == 1 and not (len(query_tokens) == 1 and partial):
            candidate = next(iter(exact.values()))
            return self._resolved(
                original,
                query,
                candidate,
                "One accepted Aves species has an exact scientific or English common-name match.",
                request_urls,
            )

        candidates = list({**partial, **exact}.values())
        candidates.sort(key=lambda item: (item.canonical_name.casefold(), item.accepted_taxon_key))
        if len(candidates) >= 2:
            return TaxonomyOutcome(
                outcome="human_selection_required",
                original_input=original,
                normalised_input=query,
                rationale=(
                    "GBIF returned multiple accepted bird species reasonably matching the "
                    "user text; no London-likely species was selected automatically."
                ),
                candidates=candidates[:12],
                request_urls=request_urls,
            )

        return TaxonomyOutcome(
            outcome="taxon_not_found",
            original_input=original,
            normalised_input=query,
            rationale=(
                "GBIF did not return an exact accepted Aves species match or enough "
                "reasonable common-name candidates for safe selection."
            ),
            request_urls=request_urls,
        )

    @staticmethod
    def _resolved(
        original: str,
        query: str,
        candidate: TaxonCandidate,
        rationale: str,
        request_urls: list[str],
    ) -> TaxonomyOutcome:
        return TaxonomyOutcome(
            outcome="resolved",
            original_input=original,
            normalised_input=query,
            matched_common_name=candidate.matched_common_name,
            scientific_name=candidate.scientific_name,
            canonical_name=candidate.canonical_name,
            accepted_taxon_key=candidate.accepted_taxon_key,
            rank=candidate.rank,
            taxonomic_status=candidate.taxonomic_status,
            class_name=candidate.class_name,
            order=candidate.order,
            family=candidate.family,
            genus=candidate.genus,
            match_method=candidate.search_method,
            match_confidence=candidate.match_confidence,
            rationale=rationale,
            request_urls=request_urls,
        )
