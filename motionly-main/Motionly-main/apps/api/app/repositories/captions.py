import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.repositories.uploads import InMemoryUploadRepository, ProjectRecord, UploadRecord
from app.schemas.captions import (
    CaptionPresetKey,
    CaptionSegment,
    CaptionStyle,
    CaptionTrackReadResponse,
    LanguageHint,
    TranscriptReadResponse,
    TranscriptStatus,
    TranscriptWord,
)
from app.services.caption_presets import get_caption_preset

logger = logging.getLogger(__name__)


@dataclass
class TranscriptRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    uploaded_video_id: UUID
    status: TranscriptStatus
    language: str
    language_hint: LanguageHint
    provider: str
    model: str
    confidence: float | None
    duration_ms: int | None
    raw_payload: dict[str, object] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    words: list[TranscriptWord] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> TranscriptReadResponse:
        return TranscriptReadResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            uploaded_video_id=self.uploaded_video_id,
            status=self.status,
            language=self.language or "unknown",
            language_hint=self.language_hint,
            provider=self.provider,
            model=self.model,
            confidence=self.confidence,
            duration_ms=self.duration_ms,
            raw_payload=self.raw_payload,
            error_code=self.error_code,
            error_message=self.error_message,
            progress_pct=_transcript_progress_pct(self.status, has_words=bool(self.words)),
            words=self.words,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class CaptionTrackRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    transcript_id: UUID
    preset: CaptionPresetKey
    schema_version: int
    style: CaptionStyle
    hide_filler_words: bool
    segments: list[CaptionSegment] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> CaptionTrackReadResponse:
        return CaptionTrackReadResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            transcript_id=self.transcript_id,
            preset=self.preset,
            schema_version=self.schema_version,
            style=self.style,
            hide_filler_words=self.hide_filler_words,
            segments=self.segments,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class InMemoryCaptionRepository:
    def __init__(self, upload_repository: InMemoryUploadRepository) -> None:
        self.upload_repository = upload_repository
        self.transcripts: dict[UUID, TranscriptRecord] = {}
        self.caption_tracks: dict[UUID, CaptionTrackRecord] = {}

    def get_project_for_workspace(self, *, project_id: UUID, workspace_id: UUID) -> ProjectRecord:
        return self.upload_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )

    def get_active_upload(self, *, project_id: UUID, workspace_id: UUID) -> UploadRecord:
        project = self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        if not project.active_video_id:
            raise ApiError(404, "active_video_missing", "Upload a source video before captions.")
        return self.upload_repository.get_upload_for_project(
            project_id=project_id,
            video_id=project.active_video_id,
            workspace_id=workspace_id,
        )

    def get_upload(self, *, project_id: UUID, video_id: UUID, workspace_id: UUID) -> UploadRecord:
        return self.upload_repository.get_upload_for_project(
            project_id=project_id,
            video_id=video_id,
            workspace_id=workspace_id,
        )

    def latest_transcript(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> TranscriptRecord | None:
        matches = [
            transcript
            for transcript in self.transcripts.values()
            if transcript.project_id == project_id and transcript.workspace_id == workspace_id
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda transcript: transcript.updated_at, reverse=True)[0]

    def get_transcript(
        self,
        *,
        project_id: UUID,
        transcript_id: UUID,
        workspace_id: UUID,
    ) -> TranscriptRecord:
        transcript = self.transcripts.get(transcript_id)
        if transcript is None or transcript.project_id != project_id:
            raise ApiError(404, "transcript_not_found", "Transcript was not found.")
        if transcript.workspace_id != workspace_id:
            raise ApiError(
                403,
                "workspace_access_denied",
                "You do not have access to this transcript.",
            )
        return transcript

    def create_transcript(self, **values: object) -> TranscriptRecord:
        transcript = TranscriptRecord(id=uuid4(), **values)
        self.transcripts[transcript.id] = transcript
        return transcript

    def update_transcript(
        self,
        *,
        transcript: TranscriptRecord,
        **values: object,
    ) -> TranscriptRecord:
        for key, value in values.items():
            setattr(transcript, key, value)
        transcript.updated_at = datetime.now(UTC)
        return transcript

    def create_caption_track(self, **values: object) -> CaptionTrackRecord:
        track = CaptionTrackRecord(id=uuid4(), **values)
        self.caption_tracks[track.id] = track
        return track

    def list_caption_tracks(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> list[CaptionTrackRecord]:
        return sorted(
            [
                track
                for track in self.caption_tracks.values()
                if track.project_id == project_id and track.workspace_id == workspace_id
            ],
            key=lambda track: track.updated_at,
            reverse=True,
        )

    def get_caption_track(
        self,
        *,
        project_id: UUID,
        track_id: UUID,
        workspace_id: UUID,
    ) -> CaptionTrackRecord:
        track = self.caption_tracks.get(track_id)
        if track is None or track.project_id != project_id:
            raise ApiError(404, "caption_track_not_found", "Caption track was not found.")
        if track.workspace_id != workspace_id:
            raise ApiError(
                403,
                "workspace_access_denied",
                "You do not have access to this caption track.",
            )
        return track

    def update_caption_track(
        self,
        *,
        track: CaptionTrackRecord,
        preset: CaptionPresetKey,
        style: CaptionStyle,
        hide_filler_words: bool,
        segments: list[CaptionSegment],
    ) -> CaptionTrackRecord:
        track.preset = preset
        track.style = style
        track.hide_filler_words = hide_filler_words
        track.segments = segments
        track.updated_at = datetime.now(UTC)
        return track

    def replace_caption_segments(
        self,
        *,
        track: CaptionTrackRecord,
        segments: list[CaptionSegment],
    ) -> CaptionTrackRecord:
        track.segments = [
            segment.model_copy(update={"index": index}) for index, segment in enumerate(segments)
        ]
        track.updated_at = datetime.now(UTC)
        return track


class SupabaseCaptionRepository(InMemoryCaptionRepository):
    def __init__(
        self,
        *,
        upload_repository: InMemoryUploadRepository,
        supabase_url: str,
        service_role_key: str,
    ) -> None:
        super().__init__(upload_repository)
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def create_transcript(self, **values: object) -> TranscriptRecord:
        transcript_id = uuid4()
        words = values.pop("words")
        response = httpx.post(
            f"{self.rest_url}/transcripts",
            headers=self.headers,
            json={**self._json_safe(values), "id": str(transcript_id)},
            timeout=10,
        )
        self._raise_for_supabase(response)
        transcript = self._transcript_from_row(response.json()[0], words=list(words))
        if transcript.words:
            httpx.post(
                f"{self.rest_url}/transcript_words",
                headers=self.headers,
                json=[
                    {
                        "workspace_id": str(transcript.workspace_id),
                        "project_id": str(transcript.project_id),
                        "transcript_id": str(transcript.id),
                        "word_index": word.index,
                        "text": word.text,
                        "start_ms": word.start_ms,
                        "end_ms": word.end_ms,
                        "confidence": word.confidence,
                    }
                    for word in transcript.words
                ],
                timeout=10,
            )
        return transcript

    def update_transcript(
        self,
        *,
        transcript: TranscriptRecord,
        **values: object,
    ) -> TranscriptRecord:
        words = values.pop("words", transcript.words)
        payload = self._json_safe({**values, "updated_at": datetime.now(UTC).isoformat()})
        response = httpx.patch(
            f"{self.rest_url}/transcripts",
            headers=self.headers,
            params={"id": f"eq.{transcript.id}", "select": "*"},
            json=payload,
            timeout=10,
        )
        self._raise_for_supabase(response)
        delete_response = httpx.delete(
            f"{self.rest_url}/transcript_words",
            headers=self.headers,
            params={"transcript_id": f"eq.{transcript.id}"},
            timeout=10,
        )
        self._raise_for_supabase(delete_response)
        next_words = list(words)
        if next_words:
            insert_response = httpx.post(
                f"{self.rest_url}/transcript_words",
                headers=self.headers,
                json=[
                    {
                        "workspace_id": str(transcript.workspace_id),
                        "project_id": str(transcript.project_id),
                        "transcript_id": str(transcript.id),
                        "word_index": word.index,
                        "text": word.text,
                        "start_ms": word.start_ms,
                        "end_ms": word.end_ms,
                        "confidence": word.confidence,
                    }
                    for word in next_words
                ],
                timeout=10,
            )
            self._raise_for_supabase(insert_response)
        return self._transcript_from_row(response.json()[0], words=next_words)

    def latest_transcript(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> TranscriptRecord | None:
        response = httpx.get(
            f"{self.rest_url}/transcripts",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "updated_at.desc",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            return None
        return self._transcript_from_row(
            rows[0],
            words=self._words_for_transcript(UUID(rows[0]["id"])),
        )

    def get_transcript(
        self,
        *,
        project_id: UUID,
        transcript_id: UUID,
        workspace_id: UUID,
    ) -> TranscriptRecord:
        response = httpx.get(
            f"{self.rest_url}/transcripts",
            headers=self.headers,
            params={
                "id": f"eq.{transcript_id}",
                "project_id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "*",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "transcript_not_found", "Transcript was not found.")
        return self._transcript_from_row(rows[0], words=self._words_for_transcript(transcript_id))

    def create_caption_track(self, **values: object) -> CaptionTrackRecord:
        segments = values.pop("segments")
        track_id = uuid4()
        response = httpx.post(
            f"{self.rest_url}/caption_tracks",
            headers=self.headers,
            json={**self._json_safe(values), "id": str(track_id)},
            timeout=10,
        )
        self._raise_for_supabase(response)
        track = self._track_from_row(response.json()[0], segments=list(segments))
        self._write_caption_segments(track)
        return track

    def list_caption_tracks(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> list[CaptionTrackRecord]:
        response = httpx.get(
            f"{self.rest_url}/caption_tracks",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "updated_at.desc",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [
            self._track_from_row(row, segments=self._segments_for_track(UUID(str(row["id"]))))
            for row in response.json()
        ]

    def get_caption_track(
        self,
        *,
        project_id: UUID,
        track_id: UUID,
        workspace_id: UUID,
    ) -> CaptionTrackRecord:
        response = httpx.get(
            f"{self.rest_url}/caption_tracks",
            headers=self.headers,
            params={
                "id": f"eq.{track_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "caption_track_not_found", "Caption track was not found.")
        return self._track_from_row(rows[0], segments=self._segments_for_track(track_id))

    def update_caption_track(
        self,
        *,
        track: CaptionTrackRecord,
        preset: CaptionPresetKey,
        style: CaptionStyle,
        hide_filler_words: bool,
        segments: list[CaptionSegment],
    ) -> CaptionTrackRecord:
        response = httpx.patch(
            f"{self.rest_url}/caption_tracks",
            headers=self.headers,
            params={"id": f"eq.{track.id}", "select": "*"},
            json={
                "preset": preset.value,
                "style": style.model_dump(mode="json"),
                "hide_filler_words": hide_filler_words,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        updated = self._track_from_row(response.json()[0], segments=segments)
        delete_response = httpx.delete(
            f"{self.rest_url}/caption_segments",
            headers=self.headers,
            params={"caption_track_id": f"eq.{track.id}"},
            timeout=10,
        )
        self._raise_for_supabase(delete_response)
        self._write_caption_segments(updated)
        return updated

    def replace_caption_segments(
        self,
        *,
        track: CaptionTrackRecord,
        segments: list[CaptionSegment],
    ) -> CaptionTrackRecord:
        next_segments = [
            segment.model_copy(update={"index": index}) for index, segment in enumerate(segments)
        ]
        response = httpx.patch(
            f"{self.rest_url}/caption_tracks",
            headers=self.headers,
            params={"id": f"eq.{track.id}", "select": "*"},
            json={"updated_at": datetime.now(UTC).isoformat()},
            timeout=10,
        )
        self._raise_for_supabase(response)
        updated = self._track_from_row(response.json()[0], segments=next_segments)
        delete_response = httpx.delete(
            f"{self.rest_url}/caption_segments",
            headers=self.headers,
            params={"caption_track_id": f"eq.{track.id}"},
            timeout=10,
        )
        self._raise_for_supabase(delete_response)
        self._write_caption_segments(updated)
        return updated

    def _words_for_transcript(self, transcript_id: UUID) -> list[TranscriptWord]:
        response = httpx.get(
            f"{self.rest_url}/transcript_words",
            headers=self.headers,
            params={
                "transcript_id": f"eq.{transcript_id}",
                "select": "*",
                "order": "word_index.asc",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [
            TranscriptWord(
                id=UUID(row["id"]),
                index=int(row["word_index"]),
                text=row["text"],
                start_ms=int(row["start_ms"]),
                end_ms=int(row["end_ms"]),
                confidence=row.get("confidence"),
            )
            for row in response.json()
        ]

    def _segments_for_track(self, track_id: UUID) -> list[CaptionSegment]:
        response = httpx.get(
            f"{self.rest_url}/caption_segments",
            headers=self.headers,
            params={
                "caption_track_id": f"eq.{track_id}",
                "select": "*",
                "order": "segment_index.asc",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [
            CaptionSegment(
                id=UUID(str(row["id"])),
                index=int(row["segment_index"]),
                start_ms=int(row["start_ms"]),
                end_ms=int(row["end_ms"]),
                text=str(row["text"]),
                line_count=int(row["line_count"]),
                words=[
                    TranscriptWord.model_validate(word)
                    for word in row.get("words", [])
                    if isinstance(word, dict)
                ],
                highlight_words=[
                    str(word) for word in row.get("highlight_words", []) if isinstance(word, str)
                ],
            )
            for row in response.json()
        ]

    def _write_caption_segments(self, track: CaptionTrackRecord) -> None:
        if not track.segments:
            return
        response = httpx.post(
            f"{self.rest_url}/caption_segments",
            headers=self.headers,
            json=[
                {
                    "workspace_id": str(track.workspace_id),
                    "project_id": str(track.project_id),
                    "caption_track_id": str(track.id),
                    "segment_index": segment.index,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    "words": [word.model_dump(mode="json") for word in segment.words],
                    "line_count": segment.line_count,
                    "highlight_words": segment.highlight_words,
                }
                for segment in track.segments
            ],
            timeout=10,
        )
        self._raise_for_supabase(response)

    def _transcript_from_row(
        self,
        row: dict[str, object],
        *,
        words: list[TranscriptWord],
    ) -> TranscriptRecord:
        return TranscriptRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            uploaded_video_id=UUID(str(row["uploaded_video_id"])),
            status=TranscriptStatus(str(row["status"])),
            language=str(row.get("language") or "unknown"),
            language_hint=LanguageHint(str(row.get("language_hint") or LanguageHint.AUTO)),
            provider=str(row["provider"]),
            model=str(row["model"]),
            confidence=row.get("confidence"),
            duration_ms=row.get("duration_ms"),
            raw_payload=row.get("raw_payload") if isinstance(row.get("raw_payload"), dict) else {},
            error_code=str(row["error_code"]) if row.get("error_code") else None,
            error_message=str(row["error_message"]) if row.get("error_message") else None,
            words=words,
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    def _track_from_row(
        self,
        row: dict[str, object],
        *,
        segments: list[CaptionSegment],
    ) -> CaptionTrackRecord:
        preset = CaptionPresetKey(str(row["preset"]))
        raw_style = row.get("style")
        style = (
            CaptionStyle.model_validate(raw_style)
            if isinstance(raw_style, dict) and raw_style
            else get_caption_preset(preset).style
        )
        return CaptionTrackRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            transcript_id=UUID(str(row["transcript_id"])),
            preset=preset,
            schema_version=int(row["schema_version"]),
            style=style,
            hide_filler_words=bool(row.get("hide_filler_words")),
            segments=segments,
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    def _json_safe(self, values: dict[str, object]) -> dict[str, object]:
        safe: dict[str, object] = {}
        for key, value in values.items():
            if isinstance(value, UUID):
                safe[key] = str(value)
            elif hasattr(value, "value"):
                safe[key] = value.value
            elif hasattr(value, "model_dump"):
                safe[key] = value.model_dump(mode="json")
            else:
                safe[key] = value
        return safe

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        logger.warning(
            "supabase_caption_request_failed",
            extra={
                "status_code": response.status_code,
                "url": str(response.request.url),
                "response": response.text[:1000],
            },
        )
        raise ApiError(502, "supabase_request_failed", "Caption data could not be saved.")


def _transcript_progress_pct(status: TranscriptStatus, *, has_words: bool) -> int:
    if status == TranscriptStatus.READY:
        return 100
    if status == TranscriptStatus.PROCESSING:
        return 65 if has_words else 55
    if status == TranscriptStatus.QUEUED:
        return 15
    if status == TranscriptStatus.CAPTIONS_UNAVAILABLE:
        return 100
    return 0
