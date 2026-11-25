"""B9: the API contract the UI is generated from (packages/shared-types)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ivsidecar import openapi

SHARED = Path(__file__).resolve().parents[3] / "packages" / "shared-types"


def test_every_route_declares_its_response():
    """A route without a response model would give the UI an `unknown` type."""
    untyped = []
    for path, methods in openapi.spec()["paths"].items():
        for method, op in methods.items():
            for code, resp in op["responses"].items():
                if not code.startswith("2") or code == "204":
                    continue
                schema = resp.get("content", {}).get("application/json", {}).get("schema", {})
                if not schema or schema.get("additionalProperties") is True:
                    untyped.append(f"{method.upper()} {path}")
    assert not untyped, f"routes without a response model: {untyped}"


@pytest.mark.parametrize("name", sorted(openapi.FILES))
def test_committed_contract_is_current(name):
    """Regenerate from the repo root with: pnpm generate"""
    assert (SHARED / name).read_text(encoding="utf-8") == openapi.FILES[name](), f"{name} is stale: run pnpm generate"
