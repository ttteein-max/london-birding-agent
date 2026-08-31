"""Stable resolver behaviour tests using synthetic GBIF-shaped responses."""

from __future__ import annotations

from typing import Any

from app.feasibility.taxonomy import GBIFBirdNameResolver


def bird_record(
    key: int,
    scientific: str,
    canonical: str,
    common_names: list[str],
) -> dict[str, Any]:
    return {
        "key": key,
        "speciesKey": key,
        "scientificName": scientific,
        "canonicalName": canonical,
        "species": canonical,
        "rank": "SPECIES",
        "taxonomicStatus": "ACCEPTED",
        "class": "Aves",
        "synonym": False,
        "vernacularNames": [
            {"vernacularName": name, "language": "eng"} for name in common_names
        ],
    }


class SyntheticGBIF:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(
        self, base_url: str, params: dict[str, Any] | None
    ) -> tuple[Any, str]:
        supplied = params or {}
        self.calls.append((base_url, supplied))
        query = supplied.get("name") or supplied.get("q")
        if base_url.endswith("/match"):
            if query == "falco subbuteo":
                return (
                    {
                        "usageKey": 2481035,
                        "scientificName": "Falco subbuteo Linnaeus, 1758",
                        "canonicalName": "Falco subbuteo",
                        "rank": "SPECIES",
                        "status": "ACCEPTED",
                        "matchType": "EXACT",
                        "confidence": 100,
                        "class": "Aves",
                    },
                    "synthetic://match/falco-subbuteo",
                )
            return (
                {
                    "usageKey": 1,
                    "scientificName": "Animalia",
                    "canonicalName": "Animalia",
                    "rank": "KINGDOM",
                    "status": "ACCEPTED",
                    "matchType": "HIGHERRANK",
                    "confidence": 99,
                },
                f"synthetic://match/{query}",
            )
        if query == "common woodpigeon":
            results = [
                bird_record(
                    2495455,
                    "Columba palumbus Linnaeus, 1758",
                    "Columba palumbus",
                    ["Common Woodpigeon"],
                )
            ]
        elif query == "robin":
            results = [
                bird_record(10, "Alpha robinensis Author", "Alpha robinensis", ["Island Robin"]),
                bird_record(11, "Beta rubra Author", "Beta rubra", ["Forest Robin"]),
            ]
        else:
            results = []
        return {"results": results, "count": len(results)}, f"synthetic://search/{query}"


def test_common_name_uses_actual_normalised_user_text() -> None:
    api = SyntheticGBIF()
    result = GBIFBirdNameResolver(api).resolve("  COMMON   woodpigeon  ")
    assert result.outcome == "resolved"
    assert result.original_input == "  COMMON   woodpigeon  "
    assert result.normalised_input == "common woodpigeon"
    assert result.matched_common_name == "Common Woodpigeon"
    assert result.canonical_name == "Columba palumbus"
    assert result.accepted_taxon_key == 2495455
    assert [params.get("name") or params.get("q") for _, params in api.calls] == [
        "common woodpigeon",
        "common woodpigeon",
    ]


def test_scientific_name_exact_match_is_resolved() -> None:
    result = GBIFBirdNameResolver(SyntheticGBIF()).resolve("Falco subbuteo")
    assert result.outcome == "resolved"
    assert result.canonical_name == "Falco subbuteo"
    assert result.match_method == "gbif_species_match"
    assert result.match_confidence == 100


def test_ambiguous_candidates_come_from_api_results() -> None:
    result = GBIFBirdNameResolver(SyntheticGBIF()).resolve("robin")
    assert result.outcome == "human_selection_required"
    assert {candidate.accepted_taxon_key for candidate in result.candidates} == {10, 11}
    assert {candidate.matched_common_name for candidate in result.candidates} == {
        "Island Robin",
        "Forest Robin",
    }


def test_unknown_or_misspelled_name_fails_safely() -> None:
    result = GBIFBirdNameResolver(SyntheticGBIF()).resolve("londun sky parrott xyz")
    assert result.outcome == "taxon_not_found"
    assert result.accepted_taxon_key is None
    assert result.candidates == []


def test_empty_name_is_not_sent_to_api() -> None:
    api = SyntheticGBIF()
    result = GBIFBirdNameResolver(api).resolve("   ")
    assert result.outcome == "taxon_not_found"
    assert api.calls == []
