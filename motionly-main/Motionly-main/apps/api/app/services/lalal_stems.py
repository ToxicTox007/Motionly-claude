"""Remote vocal/instrumental stem separation through the LALAL.AI v1 API.

Flow (https://www.lalal.ai/api/v1/docs/): upload the audio, start a
``stem_separator`` split for ``vocals``, poll ``check/`` until the task
finishes, download the stem and back tracks, then delete the source and its
tracks from LALAL storage. Only an audio-only FLAC rendition of the source is
uploaded, so video frames never leave the worker.

The separator returns the same ``{"vocals": Path, "no_vocals": Path}`` contract
as ``DemucsStemSeparator`` so the rest of the stem pipeline is unchanged.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from app.services.provider_resilience import (
    ProviderCallFailure,
    ProviderErrorInfo,
    classify_provider_exception,
    classify_provider_status,
)
from app.services.subprocess_env import media_tool_env

logger = logging.getLogger(__name__)

LALAL_BASE_URL = "https://www.lalal.ai/api/v1"
LALAL_ENDPOINT_HOST = "www.lalal.ai"
LALAL_TEMP_PREFIX = "motionly-lalal-stems-"
# Splitter model series and their published versions; the route revision pins one.
LALAL_SPLITTER_VERSIONS = {"phoenix": 3, "orion": 4, "perseus": 5, "andromeda": 6}
# LALAL track types for a two-stem split, mapped to the Demucs stem names.
_TRACK_TYPES = {"stem": "vocals", "back": "no_vocals"}
_MAX_TRACK_BYTES = 2 * 1024 * 1024 * 1024

AudioPreparer = Callable[[Path, Path], Path]


def lalal_model_revision(splitter: str) -> str:
    """Immutable route revision for a pinned splitter, e.g. ``lalal-andromeda-v6``."""
    if splitter not in LALAL_SPLITTER_VERSIONS:
        raise ValueError(
            f"LALAL splitter must be one of {', '.join(sorted(LALAL_SPLITTER_VERSIONS))}"
        )
    return f"lalal-{splitter}-v{LALAL_SPLITTER_VERSIONS[splitter]}"


def extract_flac_audio(source_path: Path, output_dir: Path) -> Path:
    """Write an audio-only, lossless 44.1 kHz stereo FLAC rendition of the source."""
    output_path = output_dir / "source-audio.flac"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "flac",
        str(output_path),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=600, env=media_tool_env()
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise _rejected("The source audio could not be prepared for stem separation.") from exc
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise _rejected("The source has no usable audio for stem separation.")
    return output_path


class LalalStemSeparator:
    provider_name = "lalal"

    def __init__(
        self,
        *,
        license_key: str,
        splitter: str = "andromeda",
        base_url: str = LALAL_BASE_URL,
        poll_interval_seconds: float = 5.0,
        max_poll_interval_seconds: float = 30.0,
        timeout_seconds: float = 1800.0,
        request_timeout_seconds: float = 60.0,
        max_track_bytes: int = _MAX_TRACK_BYTES,
        audio_preparer: AudioPreparer = extract_flac_audio,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not license_key:
            raise ValueError("LALAL license key is required")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("LALAL base_url must be an absolute HTTPS URL")
        if poll_interval_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("LALAL poll interval and timeout must be positive")
        self._license_key = license_key
        self.splitter = splitter
        self.model = f"lalal/{splitter}"
        self.model_revision = lalal_model_revision(splitter)
        self._base_url = base_url.rstrip("/")
        self._poll_interval = poll_interval_seconds
        self._max_poll_interval = max(poll_interval_seconds, max_poll_interval_seconds)
        self._timeout = timeout_seconds
        self._request_timeout = request_timeout_seconds
        self._max_track_bytes = max_track_bytes
        self._prepare_audio = audio_preparer
        self._transport = transport
        self._sleep = sleep
        self._clock = clock

    def separate(self, *, source_path: Path, model: str) -> dict[str, Path]:
        # ``model`` names a Demucs model; LALAL uses the pinned splitter instead.
        del model
        output_root = Path(tempfile.mkdtemp(prefix=LALAL_TEMP_PREFIX))
        try:
            audio_path = self._prepare_audio(source_path, output_root)
            with httpx.Client(
                timeout=httpx.Timeout(self._request_timeout, connect=10.0),
                transport=self._transport,
                trust_env=False,
            ) as client:
                source_id = self._upload(client, audio_path)
                try:
                    task_id = self._start_split(client, source_id)
                    tracks = self._wait_for_tracks(client, task_id)
                    stems = self._download_tracks(client, tracks, output_root)
                finally:
                    self._delete(client, source_id)
            audio_path.unlink(missing_ok=True)
            return stems
        except BaseException:
            shutil.rmtree(output_root, ignore_errors=True)
            raise

    # -- API calls -------------------------------------------------------------

    def _upload(self, client: httpx.Client, audio_path: Path) -> str:
        with audio_path.open("rb") as audio_file:
            payload = self._post(
                client,
                "upload/",
                content=audio_file,
                headers={
                    # A neutral name: the user's original filename is not shared.
                    "content-disposition": 'attachment; filename="motionly-audio.flac"',
                    "content-type": "application/octet-stream",
                },
            )
        source_id = payload.get("id")
        if not isinstance(source_id, str) or not source_id:
            raise _malformed()
        return source_id

    def _start_split(self, client: httpx.Client, source_id: str) -> str:
        payload = self._post(
            client,
            "split/stem_separator/",
            json={
                "source_id": source_id,
                "presets": {
                    "stem": "vocals",
                    "splitter": self.splitter,
                    "extraction_level": "deep_extraction",
                    "encoder_format": "wav",
                    "dereverb_enabled": False,
                },
                "idempotency_key": str(uuid4()),
            },
        )
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise _malformed()
        return task_id

    def _wait_for_tracks(self, client: httpx.Client, task_id: str) -> list[Mapping[str, object]]:
        deadline = self._clock() + self._timeout
        interval = self._poll_interval
        while True:
            try:
                payload = self._post(client, "check/", json={"task_ids": [task_id]})
            except ProviderCallFailure as exc:
                # The task keeps running on LALAL; transient polling errors only
                # delay the result, so keep polling until the deadline.
                if not exc.error_info.retryable:
                    raise
            else:
                tracks = _tracks_if_finished(payload, task_id)
                if tracks is not None:
                    return tracks
            if self._clock() + interval > deadline:
                raise ProviderCallFailure(
                    provider=self.provider_name,
                    error_info=ProviderErrorInfo("provider_timeout", True),
                    message="LALAL.AI did not finish stem separation in time.",
                )
            self._sleep(interval)
            interval = min(self._max_poll_interval, interval * 1.5)

    def _download_tracks(
        self,
        client: httpx.Client,
        tracks: list[Mapping[str, object]],
        output_root: Path,
    ) -> dict[str, Path]:
        urls: dict[str, str] = {}
        for track in tracks:
            if not isinstance(track, Mapping):
                raise _malformed()
            stem_name = _TRACK_TYPES.get(str(track.get("type") or ""))
            url = track.get("url")
            if stem_name is None:
                continue
            if stem_name in urls or not isinstance(url, str) or not url:
                raise _malformed()
            urls[stem_name] = _safe_download_url(url)
        if set(urls) != set(_TRACK_TYPES.values()):
            raise _malformed()
        stems: dict[str, Path] = {}
        for stem_name, url in urls.items():
            target = output_root / f"{stem_name}.wav"
            self._download(client, url, target)
            stems[stem_name] = target
        return stems

    def _download(self, client: httpx.Client, url: str, target: Path) -> None:
        written = 0
        try:
            # Signed download links carry their own authorization; the license
            # key is never sent to the download host.
            with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise ProviderCallFailure(
                        provider=self.provider_name,
                        error_info=classify_provider_status(response.status_code),
                    )
                with target.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        written += len(chunk)
                        if written > self._max_track_bytes:
                            raise _malformed()
                        output.write(chunk)
        except ProviderCallFailure:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderCallFailure(
                provider=self.provider_name,
                error_info=classify_provider_exception(exc),
            ) from None
        if written == 0:
            raise _malformed()

    def _delete(self, client: httpx.Client, source_id: str) -> None:
        """Best effort: remove the upload and its tracks from LALAL storage."""
        try:
            self._post(client, "delete/", json={"source_id": source_id})
        except Exception as exc:  # noqa: BLE001 - must not mask the split outcome
            logger.warning(
                "lalal_source_delete_failed",
                extra={"error_type": type(exc).__name__},
            )

    def _post(
        self,
        client: httpx.Client,
        operation: str,
        *,
        json: Mapping[str, object] | None = None,
        content: object = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        request_headers = {
            "accept": "application/json",
            "x-license-key": self._license_key,
            **dict(headers or {}),
        }
        try:
            response = client.post(
                f"{self._base_url}/{operation}",
                json=dict(json) if json is not None else None,
                content=content,  # type: ignore[arg-type]
                headers=request_headers,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderCallFailure(
                provider=self.provider_name,
                error_info=classify_provider_exception(exc),
            ) from None
        if response.status_code >= 400:
            raise ProviderCallFailure(
                provider=self.provider_name,
                error_info=classify_provider_status(response.status_code),
            )
        try:
            payload = response.json()
        except ValueError:
            raise _malformed() from None
        if not isinstance(payload, dict):
            raise _malformed()
        return payload


def _tracks_if_finished(
    payload: Mapping[str, object], task_id: str
) -> list[Mapping[str, object]] | None:
    """Return the tracks on success, None while running, and raise on failure."""
    results = payload.get("result")
    task = results.get(task_id) if isinstance(results, Mapping) else None
    if not isinstance(task, Mapping):
        raise _malformed()
    status = task.get("status")
    if status == "progress":
        return None
    if status == "success":
        result = task.get("result")
        tracks = result.get("tracks") if isinstance(result, Mapping) else None
        if not isinstance(tracks, list):
            raise _malformed()
        return tracks
    if status == "server_error":
        code, retryable = "provider_unavailable", True
    elif status in {"error", "cancelled"}:
        code, retryable = "provider_job_failed", False
    else:
        raise _malformed()
    raise ProviderCallFailure(
        provider="lalal",
        error_info=ProviderErrorInfo(code, retryable, fallback_eligible=False),
        message=f"LALAL.AI stem separation ended with status {status}.",
    )


def _safe_download_url(url: str) -> str:
    """Accept only LALAL-hosted links, always fetched over HTTPS."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or not (host == "lalal.ai" or host.endswith(".lalal.ai"))
    ):
        raise _malformed()
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))


def _malformed() -> ProviderCallFailure:
    return ProviderCallFailure(
        provider="lalal",
        error_info=ProviderErrorInfo(
            "provider_malformed_response",
            False,
            fallback_eligible=True,
            circuit_worthy=True,
        ),
    )


def _rejected(message: str) -> ProviderCallFailure:
    # A local problem with this source: not billable and not the provider's fault.
    return ProviderCallFailure(
        provider="lalal",
        error_info=ProviderErrorInfo(
            "provider_request_rejected",
            False,
            fallback_eligible=False,
            circuit_worthy=False,
            billable=False,
        ),
        message=message,
    )
