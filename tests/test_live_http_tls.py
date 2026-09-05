"""Regression coverage for CA verification in urllib-backed live clients."""

from __future__ import annotations

import io
import ssl
from typing import Any

from app.biodiversity import repositories
from app.feasibility import live


class _JsonResponse(io.BytesIO):
    status = 200


def _fake_urlopen(captured: dict[str, Any]):
    def open_response(
        _request: object,
        *,
        timeout: float,
        context: ssl.SSLContext,
    ) -> _JsonResponse:
        captured.update(timeout=timeout, context=context)
        return _JsonResponse(b'{"ok": true}')

    return open_response


def test_bounded_json_client_supplies_verified_ca_context(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(repositories, "urlopen", _fake_urlopen(captured))

    payload, _url = repositories.BoundedJsonClient(max_attempts=1).get_json(
        "https://example.test/data"
    )

    assert payload == {"ok": True}
    assert captured["context"].verify_mode is ssl.CERT_REQUIRED
    assert captured["context"].check_hostname is True


def test_fixture_refresh_client_supplies_verified_ca_context(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(live, "urlopen", _fake_urlopen(captured))

    payload, _url = live.get_json(
        "https://example.test/data",
        timeout=1,
        attempts=1,
    )

    assert payload == {"ok": True}
    assert captured["context"].verify_mode is ssl.CERT_REQUIRED
    assert captured["context"].check_hostname is True
