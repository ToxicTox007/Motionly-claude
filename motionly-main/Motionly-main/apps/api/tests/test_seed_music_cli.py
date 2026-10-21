"""Task 32 follow-up: the generated test-track seed CLI.

Refuses production; outside production it seeds the generated catalog via the
injected repository/storage. Tests first.
"""

from __future__ import annotations

import app.ops.seed_music_catalog as cli
from app.services.music_audio import GeneratedTrackAnalyzer


class _Settings:
    def __init__(self, app_env: str) -> None:
        self.app_env = app_env
        self.supabase_url = "https://supabase.internal"
        self.supabase_service_role_key = "service-role"
        self.media_work_dir = "/tmp/motionly-seed-test"


def test_cli_refuses_production(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: _Settings("production"))
    called = {"seeded": False}
    monkeypatch.setattr(
        cli,
        "seed_test_music_catalog",
        lambda **_kwargs: called.__setitem__("seeded", True),
    )
    assert cli.run([]) == 1
    assert called["seeded"] is False
    assert "production" in capsys.readouterr().err.lower()


def test_cli_rejects_extra_args() -> None:
    assert cli.run(["unexpected"]) == 2


def test_cli_seeds_outside_production(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: _Settings("local"))
    monkeypatch.setattr(cli, "SupabaseCatalogTrackRepository", lambda **_k: object())
    monkeypatch.setattr(cli, "create_object_storage", lambda **_k: object())

    captured: dict[str, object] = {}

    class _Result:
        imported = 3
        track_ids = ["test-track-calm", "test-track-clean", "test-track-energetic"]

    def fake_seed(*, repository, storage, environment, analyzer):
        captured["environment"] = environment
        captured["analyzer"] = analyzer
        return _Result()

    monkeypatch.setattr(cli, "seed_test_music_catalog", fake_seed)
    assert cli.run([]) == 0
    assert captured["environment"] == "local"
    assert isinstance(captured["analyzer"], GeneratedTrackAnalyzer)
    assert "Seeded 3" in capsys.readouterr().out
