from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.music import (
    MusicProviderKey,
    MusicRegion,
    MusicSelectionResponse,
    MusicSelectionStatus,
    MusicTrackResponse,
)


@dataclass
class MusicTrackRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    provider: MusicProviderKey
    provider_track_id: str
    title: str
    license: str
    duration_ms: int
    mood_tags: list[str]
    bpm: int | None
    energy: float | None
    vocals: bool
    loopable: bool
    beat_map: list[float]
    raw_metadata: dict[str, object]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> MusicTrackResponse:
        return MusicTrackResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            provider=self.provider,
            provider_track_id=self.provider_track_id,
            title=self.title,
            license=self.license,
            duration_ms=self.duration_ms,
            mood_tags=self.mood_tags,
            bpm=self.bpm,
            energy=self.energy,
            vocals=self.vocals,
            loopable=self.loopable,
            beat_map=self.beat_map,
            raw_metadata=self.raw_metadata,
            created_at=self.created_at,
        )


@dataclass
class MusicSelectionRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    edit_plan_id: UUID
    version: int
    schema_version: int
    status: MusicSelectionStatus
    music_track_id: UUID | None
    music_volume: float
    sfx_intensity: float
    rights_confirmed: bool
    cache_key: str | None
    regions: list[MusicRegion] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self, track: MusicTrackRecord | None) -> MusicSelectionResponse:
        return MusicSelectionResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            edit_plan_id=self.edit_plan_id,
            music_track_id=self.music_track_id,
            music_volume=self.music_volume,
            sfx_intensity=self.sfx_intensity,
            rights_confirmed=self.rights_confirmed,
            status=self.status,
            cache_key=self.cache_key,
            created_at=self.created_at,
            updated_at=self.updated_at,
            music_track=track.to_response() if track else None,
            regions=self.regions,
        )


class InMemoryMusicRepository:
    def __init__(self, plan_repository: InMemoryPlanRepository) -> None:
        self.plan_repository = plan_repository
        self.tracks: dict[UUID, MusicTrackRecord] = {}
        self.selections: dict[UUID, MusicSelectionRecord] = {}
        self.cache_keys: dict[tuple[UUID, UUID, str], int] = {}

    def get_project_for_workspace(self, *, project_id: UUID, workspace_id: UUID) -> None:
        self.plan_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )

    def upsert_track(self, **values: object) -> MusicTrackRecord:
        existing = next(
            (
                track
                for track in self.tracks.values()
                if track.workspace_id == values["workspace_id"]
                and track.project_id == values["project_id"]
                and track.provider == values["provider"]
                and track.provider_track_id == values["provider_track_id"]
            ),
            None,
        )
        if existing:
            existing.title = values["title"]
            existing.license = values["license"]
            existing.duration_ms = values["duration_ms"]
            existing.mood_tags = values["mood_tags"]
            existing.bpm = values.get("bpm")
            existing.energy = values.get("energy")
            existing.vocals = values["vocals"]
            existing.loopable = values["loopable"]
            existing.beat_map = values["beat_map"]
            existing.raw_metadata = values.get("raw_metadata", {})  # type: ignore[assignment]
            return existing
        track = MusicTrackRecord(id=uuid4(), **values)
        self.tracks[track.id] = track
        return track

    def get_track(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        track_id: UUID,
    ) -> MusicTrackRecord:
        track = self.tracks.get(track_id)
        if track is None or track.project_id != project_id:
            raise ApiError(404, "music_track_not_found", "Music track was not found.")
        if track.workspace_id != workspace_id:
            raise ApiError(
                403,
                "workspace_access_denied",
                "You do not have access to this music track.",
            )
        return track

    def find_cached_selection(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        cache_key: str,
    ) -> MusicSelectionRecord | None:
        version = self.cache_keys.get((workspace_id, project_id, cache_key))
        if version is None:
            return None
        matches = [
            selection
            for selection in self.selections.values()
            if selection.project_id == project_id
            and selection.workspace_id == workspace_id
            and selection.version == version
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.updated_at, reverse=True)[0]

    def latest_selection(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> MusicSelectionRecord | None:
        selections = [
            selection
            for selection in self.selections.values()
            if selection.project_id == project_id and selection.workspace_id == workspace_id
        ]
        if not selections:
            return None
        return sorted(selections, key=lambda item: item.updated_at, reverse=True)[0]

    def create_selection(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        edit_plan_id: UUID,
        status: MusicSelectionStatus,
        music_track_id: UUID | None,
        music_volume: float,
        sfx_intensity: float,
        rights_confirmed: bool,
        cache_key: str | None,
        regions: list[MusicRegion] | None = None,
    ) -> MusicSelectionRecord:
        version = (
            max(
                [
                    selection.version
                    for selection in self.selections.values()
                    if selection.project_id == project_id and selection.workspace_id == workspace_id
                ],
                default=0,
            )
            + 1
        )
        record = MusicSelectionRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            edit_plan_id=edit_plan_id,
            version=version,
            schema_version=1,
            status=status,
            music_track_id=music_track_id,
            music_volume=music_volume,
            sfx_intensity=sfx_intensity,
            rights_confirmed=rights_confirmed,
            cache_key=cache_key,
            regions=regions or [],
        )
        self.selections[record.id] = record
        if cache_key:
            self.cache_keys[(workspace_id, project_id, cache_key)] = version
        return record

    def response_for_selection(self, selection: MusicSelectionRecord) -> MusicSelectionResponse:
        track = self.tracks.get(selection.music_track_id) if selection.music_track_id else None
        return selection.to_response(track)

    def update_regions(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        selection_id: UUID,
        regions: list[MusicRegion],
    ) -> MusicSelectionRecord:
        selection = next(
            (
                item
                for item in self.selections.values()
                if item.id == selection_id
                and item.project_id == project_id
                and item.workspace_id == workspace_id
            ),
            None,
        )
        if selection is None:
            raise ApiError(404, "music_selection_not_found", "Music selection was not found.")
        selection.regions = regions
        selection.updated_at = datetime.now(UTC)
        return selection


class SupabaseMusicRepository(InMemoryMusicRepository):
    def __init__(
        self,
        *,
        plan_repository: InMemoryPlanRepository,
        supabase_url: str,
        service_role_key: str,
    ) -> None:
        super().__init__(plan_repository)
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def upsert_track(self, **values: object) -> MusicTrackRecord:
        response = httpx.post(
            f"{self.rest_url}/music_tracks",
            headers={**self.headers, "prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "workspace_id,project_id,provider,provider_track_id"},
            json=self._json_safe(values),
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._track_from_row(response.json()[0])

    def get_track(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        track_id: UUID,
    ) -> MusicTrackRecord:
        response = httpx.get(
            f"{self.rest_url}/music_tracks",
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
            raise ApiError(404, "music_track_not_found", "Music track was not found.")
        return self._track_from_row(rows[0])

    def create_selection(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        edit_plan_id: UUID,
        status: MusicSelectionStatus,
        music_track_id: UUID | None,
        music_volume: float,
        sfx_intensity: float,
        rights_confirmed: bool,
        cache_key: str | None,
        regions: list[MusicRegion] | None = None,
    ) -> MusicSelectionRecord:
        version = self._next_selection_version(project_id=project_id, workspace_id=workspace_id)
        payload = {
            "workspace_id": str(workspace_id),
            "project_id": str(project_id),
            "edit_plan_id": str(edit_plan_id),
            "version": version,
            "schema_version": 1,
            "status": status.value,
            "music_track_id": str(music_track_id) if music_track_id else None,
            "music_volume": music_volume,
            "sfx_intensity": sfx_intensity,
            "rights_confirmed": rights_confirmed,
            "cache_key": cache_key,
            "regions": [region.model_dump(mode="json") for region in (regions or [])],
        }
        response = httpx.post(
            f"{self.rest_url}/project_music_selections",
            headers=self.headers,
            json=[self._json_safe(payload)],
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._selection_from_row(response.json()[0])

    def get_selection(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        selection_id: UUID,
    ) -> MusicSelectionRecord:
        response = httpx.get(
            f"{self.rest_url}/project_music_selections",
            headers=self.headers,
            params={
                "id": f"eq.{selection_id}",
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
            raise ApiError(404, "music_selection_not_found", "Music selection was not found.")
        return self._selection_from_row(rows[0])

    def find_cached_selection(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        cache_key: str,
    ) -> MusicSelectionRecord | None:
        response = httpx.get(
            f"{self.rest_url}/project_music_selections",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "cache_key": f"eq.{cache_key}",
                "select": "*",
                "order": "updated_at.desc",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._selection_from_row(rows[0]) if rows else None

    def latest_selection(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> MusicSelectionRecord | None:
        response = httpx.get(
            f"{self.rest_url}/project_music_selections",
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
        return self._selection_from_row(rows[0]) if rows else None

    def response_for_selection(self, selection: MusicSelectionRecord) -> MusicSelectionResponse:
        track = (
            self.get_track(
                project_id=selection.project_id,
                workspace_id=selection.workspace_id,
                track_id=selection.music_track_id,
            )
            if selection.music_track_id
            else None
        )
        return selection.to_response(track)

    def update_regions(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        selection_id: UUID,
        regions: list[MusicRegion],
    ) -> MusicSelectionRecord:
        response = httpx.patch(
            f"{self.rest_url}/project_music_selections",
            headers=self.headers,
            params={
                "id": f"eq.{selection_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
            },
            json={"regions": [region.model_dump(mode="json") for region in regions]},
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "music_selection_not_found", "Music selection was not found.")
        return self._selection_from_row(rows[0])

    def _next_selection_version(self, *, project_id: UUID, workspace_id: UUID) -> int:
        response = httpx.get(
            f"{self.rest_url}/project_music_selections",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "version",
                "order": "version.desc",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return int(rows[0]["version"]) + 1 if rows else 1

    def _track_from_row(self, row: dict[str, object]) -> MusicTrackRecord:
        return MusicTrackRecord(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            provider=MusicProviderKey(row["provider"]),
            provider_track_id=row["provider_track_id"],
            title=row["title"],
            license=row["license"],
            duration_ms=int(row["duration_ms"]),
            mood_tags=row.get("mood_tags") or [],
            bpm=int(row["bpm"]) if row.get("bpm") is not None else None,
            energy=float(row["energy"]) if row.get("energy") is not None else None,
            vocals=bool(row["vocals"]),
            loopable=bool(row["loopable"]),
            beat_map=row.get("beat_map") or [],
            raw_metadata=row.get("raw_metadata") or {},
            created_at=row["created_at"],
        )

    def _selection_from_row(self, row: dict[str, object]) -> MusicSelectionRecord:
        raw_regions = row.get("regions") or []
        regions = (
            [MusicRegion(**item) for item in raw_regions] if isinstance(raw_regions, list) else []
        )
        return MusicSelectionRecord(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            edit_plan_id=UUID(row["edit_plan_id"]),
            version=int(row["version"]),
            schema_version=int(row["schema_version"]),
            status=MusicSelectionStatus(row["status"]),
            music_track_id=UUID(row["music_track_id"]) if row.get("music_track_id") else None,
            music_volume=float(row["music_volume"]),
            sfx_intensity=float(row["sfx_intensity"]),
            rights_confirmed=bool(row["rights_confirmed"]),
            cache_key=row.get("cache_key"),
            regions=regions,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _json_safe(self, values: dict[str, object]) -> dict[str, object]:
        return {k: (str(v) if isinstance(v, UUID) else v) for k, v in values.items()}

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if not response.is_success:
            raise ApiError(502, "supabase_request_failed", "Supabase request failed.")
