"""Container provenance and per-install helper secrets (OPS-12).

Pure file checks plus the fresh-``.env`` writer on ``tmp_path``: no Docker,
no DB, never skips.

* Every image ``docker-compose.yml`` names, and the base of
  ``Dockerfile.mpd``, is pinned by digest, so a retagged upstream can't
  slip in on the next ``docker compose up`` or MPD rebuild.
* The Letta server password and the SearXNG secret come from ``.env``
  (with the historical values only as the fallback for an older ``.env``),
  and a fresh ``.env`` carries random ones. Letta's password is the same
  variable the core reads (``LETTA_TOKEN``), so the two can't drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from domovoi.env_bootstrap import ensure_env_file, render_fresh_env

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "domovoi" / "docker-compose.yml"
DOCKERFILE_MPD = REPO_ROOT / "domovoi" / "Dockerfile.mpd"
ENV_EXAMPLE = REPO_ROOT / "domovoi" / ".env.example"

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")

HISTORICAL = {"domovoi-local", "domovoi-local-not-load-bearing"}


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def _kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


# ─── images ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("service", sorted(yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]))
def test_every_compose_image_is_pinned_by_digest(service: str) -> None:
    image = str(_services()[service]["image"])
    assert DIGEST.search(image), f"{service} runs {image!r}, not a digest"
    assert ":latest@" not in image, f"{service} names a floating tag beside its digest"


def test_mpd_image_base_is_pinned_by_digest() -> None:
    froms = [
        line.split()[1]
        for line in DOCKERFILE_MPD.read_text(encoding="utf-8").splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    assert froms, "Dockerfile.mpd has no FROM"
    for ref in froms:
        assert DIGEST.search(ref), f"Dockerfile.mpd builds FROM {ref!r}, not a digest"


# ─── helper secrets ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("service", "key", "variable"),
    [
        ("letta", "LETTA_SERVER_PASSWORD", "LETTA_TOKEN"),
        ("searxng", "SEARXNG_SECRET", "SEARXNG_SECRET"),
    ],
)
def test_compose_takes_the_helper_secrets_from_env(service: str, key: str, variable: str) -> None:
    value = str(_services()[service]["environment"][key])
    m = re.fullmatch(r"\$\{(?P<var>[A-Z_]+):-(?P<fallback>[^}]*)\}", value)
    assert m, f"{service}.{key} is the literal {value!r}, not a ${{...}} from .env"
    assert m.group("var") == variable
    # The fallback only keeps an older .env working; it is never the fresh value.
    assert m.group("fallback") in HISTORICAL


def _fresh_env(tmp_path: Path) -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    example = tmp_path / ".env.example"
    example.write_bytes(ENV_EXAMPLE.read_bytes())
    env_path = tmp_path / ".env"
    ensure_env_file(env_path, example, volume_exists=lambda: False)
    return _kv(env_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("variable", ["LETTA_TOKEN", "SEARXNG_SECRET"])
def test_fresh_env_carries_a_random_helper_secret(tmp_path: Path, variable: str) -> None:
    kv = _fresh_env(tmp_path)
    value = kv[variable]
    assert value not in HISTORICAL
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", value), value


def test_fresh_env_helper_secrets_differ_per_install_and_from_each_other(tmp_path: Path) -> None:
    a = _fresh_env(tmp_path / "a")
    b = _fresh_env(tmp_path / "b")
    assert a["LETTA_TOKEN"] != b["LETTA_TOKEN"]
    assert a["SEARXNG_SECRET"] != b["SEARXNG_SECRET"]
    assert a["LETTA_TOKEN"] != a["SEARXNG_SECRET"]
    assert a["LETTA_TOKEN"] != a["POSTGRES_PASSWORD"]


def test_helper_secrets_are_fresh_even_when_the_postgres_default_is_kept(tmp_path: Path) -> None:
    """A re-clone beside an existing pgdata volume keeps the historical
    Postgres password; nothing ties the helper secrets to that volume."""
    example = tmp_path / ".env.example"
    example.write_bytes(ENV_EXAMPLE.read_bytes())
    env_path = tmp_path / ".env"
    result = ensure_env_file(env_path, example, volume_exists=lambda: True)
    assert result.reused_default
    kv = _kv(env_path.read_text(encoding="utf-8"))
    assert kv["LETTA_TOKEN"] not in HISTORICAL
    assert kv["SEARXNG_SECRET"] not in HISTORICAL


def test_render_fresh_env_writes_the_given_helper_secrets() -> None:
    text = render_fresh_env(
        ENV_EXAMPLE.read_text(encoding="utf-8"), "pw", letta_token="L" * 32, searxng_secret="S" * 32
    )
    kv = _kv(text)
    assert kv["LETTA_TOKEN"] == "L" * 32
    assert kv["SEARXNG_SECRET"] == "S" * 32


def test_the_example_leaves_letta_token_commented() -> None:
    """The generated line is the only live LETTA_TOKEN in a fresh .env."""
    assert "LETTA_TOKEN" not in _kv(ENV_EXAMPLE.read_text(encoding="utf-8"))
    assert "SEARXNG_SECRET" not in _kv(ENV_EXAMPLE.read_text(encoding="utf-8"))
