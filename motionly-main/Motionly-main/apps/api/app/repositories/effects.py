from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.effects import (
    EFFECTS_SCHEMA_VERSION,
    AfterEffectsPreset,
    BackgroundRemovalSettings,
    EffectsConfigResponse,
    EffectsStatus,
    EffectsTextureSettings,
    GradientRampSettings,
    MotionEffectKey,
    VisualPresetKey,
)


@dataclass
class EffectsConfigRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    edit_plan_id: UUID | None
    version: int
    schema_version: int
    status: EffectsStatus
    preset: VisualPresetKey
    lut_intensity: float
    texture: EffectsTextureSettings
    motion_effects: list[MotionEffectKey]
    background_removal: BackgroundRemovalSettings
    gradient_ramp: GradientRampSettings
    after_effects_presets: list[AfterEffectsPreset]
    after_effects_project_url: str | None
    correction_manifest_url: str | None
    effect_count_limit_per_30s: int
    render_time_impact: str
    preview_before_url: str | None
    preview_after_url: str | None
    cache_key: str | None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> EffectsConfigResponse:
        return EffectsConfigResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            edit_plan_id=self.edit_plan_id,
            version=self.version,
            schema_version=self.schema_version,
            status=self.status,
            preset=self.preset,
            lut_intensity=self.lut_intensity,
            texture=self.texture,
            motion_effects=self.motion_effects,
            background_removal=self.background_removal,
            gradient_ramp=self.gradient_ramp,
            after_effects_presets=self.after_effects_presets,
            after_effects_project_url=self.after_effects_project_url,
            correction_manifest_url=self.correction_manifest_url,
            effect_count_limit_per_30s=self.effect_count_limit_per_30s,
            render_time_impact=self.render_time_impact,  # type: ignore[arg-type]
            preview_before_url=self.preview_before_url,
            preview_after_url=self.preview_after_url,
            cache_key=self.cache_key,
            error_code=self.error_code,
            error_message=self.error_message,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class InMemoryEffectsRepository:
    def __init__(self, plan_repository: InMemoryPlanRepository) -> None:
        self.plan_repository = plan_repository
        self.configs: dict[UUID, EffectsConfigRecord] = {}
        self.cache_keys: dict[tuple[UUID, UUID, str], int] = {}

    def get_project_for_workspace(self, *, project_id: UUID, workspace_id: UUID) -> None:
        self.plan_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )

    def find_cached_config(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        cache_key: str,
    ) -> EffectsConfigRecord | None:
        version = self.cache_keys.get((workspace_id, project_id, cache_key))
        if version is None:
            return None
        matches = [
            config
            for config in self.configs.values()
            if config.project_id == project_id
            and config.workspace_id == workspace_id
            and config.version == version
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.updated_at, reverse=True)[0]

    def latest_config(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> EffectsConfigRecord | None:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        configs = [
            config
            for config in self.configs.values()
            if config.project_id == project_id and config.workspace_id == workspace_id
        ]
        if not configs:
            return None
        return sorted(configs, key=lambda item: item.updated_at, reverse=True)[0]

    def create_config(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        edit_plan_id: UUID | None,
        status: EffectsStatus,
        preset: VisualPresetKey,
        lut_intensity: float,
        texture: EffectsTextureSettings,
        motion_effects: list[MotionEffectKey],
        background_removal: BackgroundRemovalSettings,
        gradient_ramp: GradientRampSettings,
        after_effects_presets: list[AfterEffectsPreset],
        after_effects_project_url: str | None,
        correction_manifest_url: str | None,
        effect_count_limit_per_30s: int,
        render_time_impact: str,
        preview_before_url: str | None,
        preview_after_url: str | None,
        cache_key: str | None,
    ) -> EffectsConfigRecord:
        version = (
            max(
                [
                    config.version
                    for config in self.configs.values()
                    if config.project_id == project_id and config.workspace_id == workspace_id
                ],
                default=0,
            )
            + 1
        )
        record = EffectsConfigRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            edit_plan_id=edit_plan_id,
            version=version,
            schema_version=EFFECTS_SCHEMA_VERSION,
            status=status,
            preset=preset,
            lut_intensity=lut_intensity,
            texture=texture,
            motion_effects=motion_effects,
            background_removal=background_removal,
            gradient_ramp=gradient_ramp,
            after_effects_presets=after_effects_presets,
            after_effects_project_url=after_effects_project_url,
            correction_manifest_url=correction_manifest_url,
            effect_count_limit_per_30s=effect_count_limit_per_30s,
            render_time_impact=render_time_impact,
            preview_before_url=preview_before_url,
            preview_after_url=preview_after_url,
            cache_key=cache_key,
        )
        self.configs[record.id] = record
        if cache_key:
            self.cache_keys[(workspace_id, project_id, cache_key)] = version
        return record

    def response_for_config(self, config: EffectsConfigRecord) -> EffectsConfigResponse:
        return config.to_response()

    def next_version(self, *, project_id: UUID, workspace_id: UUID) -> int:
        return (
            max(
                [
                    config.version
                    for config in self.configs.values()
                    if config.project_id == project_id and config.workspace_id == workspace_id
                ],
                default=0,
            )
            + 1
        )


class SupabaseEffectsRepository(InMemoryEffectsRepository):
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

    def create_config(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        edit_plan_id: UUID | None,
        status: EffectsStatus,
        preset: VisualPresetKey,
        lut_intensity: float,
        texture: EffectsTextureSettings,
        motion_effects: list[MotionEffectKey],
        background_removal: BackgroundRemovalSettings,
        gradient_ramp: GradientRampSettings,
        after_effects_presets: list[AfterEffectsPreset],
        after_effects_project_url: str | None,
        correction_manifest_url: str | None,
        effect_count_limit_per_30s: int,
        render_time_impact: str,
        preview_before_url: str | None,
        preview_after_url: str | None,
        cache_key: str | None,
    ) -> EffectsConfigRecord:
        version = self._next_version(project_id=project_id, workspace_id=workspace_id)
        payload = {
            "workspace_id": str(workspace_id),
            "project_id": str(project_id),
            "edit_plan_id": str(edit_plan_id) if edit_plan_id else None,
            "version": version,
            "schema_version": EFFECTS_SCHEMA_VERSION,
            "status": status.value,
            "preset": preset.value,
            "lut_intensity": lut_intensity,
            "texture": texture.model_dump(mode="json"),
            "motion_effects": [effect.value for effect in motion_effects],
            "background_removal": background_removal.model_dump(mode="json"),
            "gradient_ramp": gradient_ramp.model_dump(mode="json"),
            "after_effects_presets": [
                preset.model_dump(mode="json") for preset in after_effects_presets
            ],
            "after_effects_project_url": after_effects_project_url,
            "correction_manifest_url": correction_manifest_url,
            "effect_count_limit_per_30s": effect_count_limit_per_30s,
            "render_time_impact": render_time_impact,
            "preview_before_url": preview_before_url,
            "preview_after_url": preview_after_url,
            "cache_key": cache_key,
        }
        response = httpx.post(
            f"{self.rest_url}/visual_presets",
            headers=self.headers,
            json=[payload],
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._config_from_row(response.json()[0])

    def find_cached_config(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        cache_key: str,
    ) -> EffectsConfigRecord | None:
        response = httpx.get(
            f"{self.rest_url}/visual_presets",
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
        return self._config_from_row(rows[0]) if rows else None

    def latest_config(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> EffectsConfigRecord | None:
        response = httpx.get(
            f"{self.rest_url}/visual_presets",
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
        return self._config_from_row(rows[0]) if rows else None

    def _next_version(self, *, project_id: UUID, workspace_id: UUID) -> int:
        response = httpx.get(
            f"{self.rest_url}/visual_presets",
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

    def next_version(self, *, project_id: UUID, workspace_id: UUID) -> int:
        return self._next_version(project_id=project_id, workspace_id=workspace_id)

    def _config_from_row(self, row: dict[str, object]) -> EffectsConfigRecord:
        return EffectsConfigRecord(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            edit_plan_id=UUID(row["edit_plan_id"]) if row.get("edit_plan_id") else None,
            version=int(row["version"]),
            schema_version=int(row["schema_version"]),
            status=EffectsStatus(row["status"]),
            preset=VisualPresetKey(row["preset"]),
            lut_intensity=float(row["lut_intensity"]),
            texture=EffectsTextureSettings.model_validate(row.get("texture") or {}),
            motion_effects=[MotionEffectKey(effect) for effect in row.get("motion_effects") or []],
            background_removal=BackgroundRemovalSettings.model_validate(
                row.get("background_removal") or {}
            ),
            gradient_ramp=GradientRampSettings.model_validate(row.get("gradient_ramp") or {}),
            after_effects_presets=[
                AfterEffectsPreset.model_validate(preset)
                for preset in row.get("after_effects_presets") or []
            ],
            after_effects_project_url=row.get("after_effects_project_url"),
            correction_manifest_url=row.get("correction_manifest_url"),
            effect_count_limit_per_30s=int(row["effect_count_limit_per_30s"]),
            render_time_impact=row["render_time_impact"],
            preview_before_url=row.get("preview_before_url"),
            preview_after_url=row.get("preview_after_url"),
            cache_key=row.get("cache_key"),
            error_code=row.get("error_code"),
            error_message=row.get("error_message"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if not response.is_success:
            raise ApiError(502, "supabase_request_failed", "Supabase request failed.")
