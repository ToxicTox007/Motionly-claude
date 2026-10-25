"""Tests for app.services.inference_secrets (plan Task 5, Section 2.4)."""

from __future__ import annotations

import json
import os

import pytest

from app.core.config import Settings
from app.core.logging import SECRET_VALUE_MARKER, registered_secret_values
from app.services.inference_secrets import (
    SecretResolutionError,
    SecretResolver,
    SecretValue,
    parse_secret_ref,
)


def _settings(**overrides) -> Settings:
    base = {
        "APP_ENV": "test",
        "SUPABASE_JWT_SECRET": "x" * 32,
        "SUPABASE_URL": "https://supabase.invalid",
        "SUPABASE_SERVICE_ROLE_KEY": "y" * 32,
    }
    base.update(overrides)
    return Settings(_env_file=None, **{k: v for k, v in base.items()})


# -- parse_secret_ref -------------------------------------------------------


def test_parse_secret_ref_rejects_missing_scheme() -> None:
    with pytest.raises(SecretResolutionError):
        parse_secret_ref("no-scheme")


def test_parse_secret_ref_rejects_empty_remainder() -> None:
    with pytest.raises(SecretResolutionError) as exc:
        parse_secret_ref("file:")
    assert "file:" in str(exc.value)


def test_parse_secret_ref_rejects_unknown_scheme() -> None:
    with pytest.raises(SecretResolutionError) as exc:
        parse_secret_ref("vault:thing")
    assert "vault" in str(exc.value)


def test_parse_secret_ref_accepts_each_valid_scheme() -> None:
    assert parse_secret_ref("file:a") == ("file", "a")
    assert parse_secret_ref("bundle:a") == ("bundle", "a")
    assert parse_secret_ref("env:A") == ("env", "A")
    assert parse_secret_ref("db:a") == ("db", "a")


# -- file: refs -------------------------------------------------------------


def test_file_ref_reads_and_strips_trailing_newline(tmp_path) -> None:
    (tmp_path / "route-secret").write_text("super-secret-token\n", encoding="utf-8")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_DIR=str(tmp_path)))
    assert resolver.resolve("file:route-secret") == "super-secret-token"


def test_file_ref_registers_value_with_redactor(tmp_path) -> None:
    value = "file-secret-abcdef123456"
    (tmp_path / "s").write_text(value, encoding="utf-8")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_DIR=str(tmp_path)))
    resolver.resolve("file:s")
    assert value in registered_secret_values()


def test_file_ref_caches(tmp_path) -> None:
    f = tmp_path / "s"
    f.write_text("first-value-abcdef", encoding="utf-8")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_DIR=str(tmp_path)))
    first = resolver.resolve("file:s")
    f.write_text("second-value-abcdef", encoding="utf-8")
    assert resolver.resolve("file:s") == first  # cached, not re-read


def test_file_ref_missing_file_names_ref_not_value(tmp_path) -> None:
    resolver = SecretResolver(settings=_settings(AI_SECRETS_DIR=str(tmp_path)))
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve("file:absent")
    assert "file:absent" in str(exc.value)


def test_file_ref_empty_value_rejected(tmp_path) -> None:
    (tmp_path / "empty").write_text("\n", encoding="utf-8")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_DIR=str(tmp_path)))
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve("file:empty")
    assert "empty value" in str(exc.value)


def test_file_ref_path_traversal_rejected(tmp_path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (tmp_path / "outside").write_text("leak", encoding="utf-8")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_DIR=str(secrets_dir)))
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve("file:../outside")
    assert "escapes" in str(exc.value)


def test_file_ref_symlink_escape_rejected(tmp_path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    target = tmp_path / "outside"
    target.write_text("leak", encoding="utf-8")
    link = secrets_dir / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks not supported on this platform")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_DIR=str(secrets_dir)))
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve("file:link")
    assert "escapes" in str(exc.value)


# -- bundle: refs -----------------------------------------------------------


def test_bundle_ref_reads_key(tmp_path) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"op-key": "bundle-value-abcdef"}), encoding="utf-8")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_BUNDLE_FILE=str(bundle)))
    assert resolver.resolve("bundle:op-key") == "bundle-value-abcdef"
    assert "bundle-value-abcdef" in registered_secret_values()


def test_bundle_ref_missing_key(tmp_path) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"a": "b" * 8}), encoding="utf-8")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_BUNDLE_FILE=str(bundle)))
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve("bundle:absent")
    assert "bundle:absent" in str(exc.value)


def test_bundle_ref_non_object_bundle(tmp_path) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_BUNDLE_FILE=str(bundle)))
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve("bundle:a")
    assert "JSON object" in str(exc.value)


def test_bundle_ref_non_string_value(tmp_path) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"a": 123}), encoding="utf-8")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_BUNDLE_FILE=str(bundle)))
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve("bundle:a")
    assert "string value" in str(exc.value)


def test_bundle_ref_without_bundle_file() -> None:
    resolver = SecretResolver(settings=_settings())
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve("bundle:a")
    assert "AI_SECRETS_BUNDLE_FILE" in str(exc.value)


# -- env: refs --------------------------------------------------------------


def test_env_ref_rejected_in_production(monkeypatch) -> None:
    monkeypatch.setenv("SOME_SECRET", "env-secret-abcdef")
    resolver = SecretResolver(settings=_settings(APP_ENV="production"))
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve("env:SOME_SECRET")
    assert "rejected in production" in str(exc.value)


def test_env_ref_allowed_outside_production_with_warning(monkeypatch, caplog) -> None:
    monkeypatch.setenv("SOME_SECRET", "env-secret-abcdef")
    resolver = SecretResolver(settings=_settings(APP_ENV="development"))
    with caplog.at_level("WARNING"):
        assert resolver.resolve("env:SOME_SECRET") == "env-secret-abcdef"
    assert any("env_ref" in r.message for r in caplog.records)


def test_env_ref_missing_var(monkeypatch) -> None:
    monkeypatch.delenv("ABSENT_SECRET", raising=False)
    resolver = SecretResolver(settings=_settings(APP_ENV="development"))
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve("env:ABSENT_SECRET")
    assert "env:ABSENT_SECRET" in str(exc.value)


# -- db: refs ---------------------------------------------------------------


def test_db_ref_without_opener_is_an_error() -> None:
    resolver = SecretResolver(settings=_settings())
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve("db:route-secret")
    # Task 20: db: refs resolve through the database secret store. Without an
    # opener wired (no KEK / DB registry configured) the ref is a clear error.
    assert "database secret store" in str(exc.value)


# -- missing / invalid refs -------------------------------------------------


def test_resolve_rejects_missing_scheme() -> None:
    resolver = SecretResolver(settings=_settings())
    with pytest.raises(SecretResolutionError):
        resolver.resolve("plain-name")


# -- SecretValue wrapper and resolver repr ----------------------------------


def test_secret_value_repr_and_str_are_redacted() -> None:
    value = SecretValue("the-raw-secret-abcdef")
    assert "the-raw-secret-abcdef" not in repr(value)
    assert "the-raw-secret-abcdef" not in str(value)
    assert SECRET_VALUE_MARKER in repr(value)
    assert value.reveal() == "the-raw-secret-abcdef"


def test_resolve_value_wraps_and_registers(tmp_path) -> None:
    (tmp_path / "s").write_text("wrapped-secret-abcdef", encoding="utf-8")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_DIR=str(tmp_path)))
    wrapped = resolver.resolve_value("file:s")
    assert isinstance(wrapped, SecretValue)
    assert wrapped.reveal() == "wrapped-secret-abcdef"
    assert "wrapped-secret-abcdef" not in repr(wrapped)


def test_resolver_repr_hides_names_and_values(tmp_path) -> None:
    (tmp_path / "very-telling-name").write_text("the-value-abcdef", encoding="utf-8")
    resolver = SecretResolver(settings=_settings(AI_SECRETS_DIR=str(tmp_path)))
    resolver.resolve("file:very-telling-name")
    rendered = repr(resolver)
    assert "very-telling-name" not in rendered
    assert "the-value-abcdef" not in rendered
    assert "cached=1" in rendered


def test_os_env_not_leaked_by_default() -> None:
    # Sanity: resolving a file ref must not fall back to the environment.
    os.environ.pop("NON_EXISTENT_REF", None)
    resolver = SecretResolver(settings=_settings())
    with pytest.raises(SecretResolutionError):
        resolver.resolve("file:NON_EXISTENT_REF")
