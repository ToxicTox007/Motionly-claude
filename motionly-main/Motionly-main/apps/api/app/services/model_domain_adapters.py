from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path

from app.services.model_gateway import (
    AdapterResult,
    AudioIntelligenceOutput,
    AudioIntelligenceRequest,
    EmbeddingsOutput,
    GeneratedArtifact,
    GenerationOperationOutput,
    GenerationOutput,
    ImageEmbeddingsRequest,
    MatteSegmentationOutput,
    MatteSegmentationRequest,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelRequest,
    ModelRoute,
    ModelUsage,
    StemSeparationOutput,
    StemSeparationRequest,
    VideoGenerationPollRequest,
    VideoGenerationSubmitRequest,
    VisualManifestOutput,
    VisualManifestRequest,
)
from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo

logger = logging.getLogger(__name__)


class LocalVisualManifestAdapter:
    def __init__(self, provider: object) -> None:
        self.provider = provider

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        del route
        if not isinstance(request, VisualManifestRequest):
            raise TypeError("local visual adapter requires VisualManifestRequest")
        manifest = self.provider.analyze(
            source_path=request.source_path,
            metadata=request.metadata,
            sample_rate_fps=request.sample_rate_fps,
            scene_threshold=request.scene_threshold,
        )
        return AdapterResult(
            output=VisualManifestOutput(manifest=manifest),
            usage=ModelUsage(input_units=max(1, request.source_path.stat().st_size // 1024)),
        )


class GatewayVisualIntelligenceProvider:
    def __init__(self, *, gateway: ModelGateway, route: ModelRoute) -> None:
        self.gateway = gateway
        self.route = route
        self.provider_name = route.provider
        self.model = route.model

    def analyze(self, *, source_path, metadata, sample_rate_fps, scene_threshold):
        return self.analyze_with_context(
            source_path=source_path,
            metadata=metadata,
            sample_rate_fps=sample_rate_fps,
            scene_threshold=scene_threshold,
            workspace_id=None,
            project_id=None,
        )

    def analyze_with_context(
        self,
        *,
        source_path,
        metadata,
        sample_rate_fps,
        scene_threshold,
        workspace_id,
        project_id,
    ):
        result = self.gateway.invoke(
            VisualManifestRequest(
                source_path=source_path,
                metadata=metadata,
                sample_rate_fps=sample_rate_fps,
                scene_threshold=scene_threshold,
            ),
            context=ModelCallContext(
                workspace_id=workspace_id,
                project_id=project_id,
            ),
        )
        if not isinstance(result.output, VisualManifestOutput):
            raise TypeError("visual manifest route returned the wrong typed output")
        return result.output.manifest


class LocalAudioIntelligenceAdapter:
    def __init__(self, providers: Mapping[str, object]) -> None:
        self.providers = dict(providers)

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        del route
        if not isinstance(request, AudioIntelligenceRequest):
            raise TypeError("local audio adapter requires AudioIntelligenceRequest")
        insights: dict[str, object] = {}
        diarization = self.providers.get("speaker_diarization")
        if diarization is not None and hasattr(diarization, "diarize"):
            try:
                insights["speaker_diarization"] = diarization.diarize(
                    audio_path=request.audio_path
                )
            except Exception as exc:
                logger.error(
                    "audio_ml_provider_failed",
                    extra={
                        "provider": "speaker_diarization",
                        "error_type": type(exc).__name__,
                    },
                )
                insights["speaker_diarization"] = {
                    "provider": getattr(diarization, "provider_name", "pyannote"),
                    "model": getattr(diarization, "model", "unknown"),
                    "speaker_count": 0,
                    "segments": [],
                    "confidence": None,
                    "degraded": True,
                }
        emotion = self.providers.get("emotion")
        if emotion is not None and hasattr(emotion, "classify_windows"):
            try:
                insights["emotion_windows"] = emotion.classify_windows(
                    audio_path=request.audio_path
                )
            except Exception as exc:
                logger.error(
                    "audio_ml_provider_failed",
                    extra={"provider": "emotion", "error_type": type(exc).__name__},
                )
                insights["emotion_windows"] = []
        sound = self.providers.get("sound")
        if sound is not None and hasattr(sound, "classify"):
            try:
                insights["sound_classification"] = sound.classify(
                    audio_path=request.audio_path
                )
            except Exception as exc:
                logger.error(
                    "audio_ml_provider_failed",
                    extra={"provider": "sound", "error_type": type(exc).__name__},
                )
                insights["sound_classification"] = {
                    "provider": getattr(sound, "provider_name", "yamnet"),
                    "model": getattr(sound, "model_url", "unknown"),
                    "labels": [],
                    "degraded": True,
                }
        return AdapterResult(
            output=AudioIntelligenceOutput(insights=insights),
            usage=ModelUsage(input_units=max(1, request.audio_path.stat().st_size // 32)),
        )


class GatewayAudioIntelligenceProvider:
    def __init__(self, *, gateway: ModelGateway, route: ModelRoute) -> None:
        self.gateway = gateway
        self.route = route
        self.provider_name = route.provider
        self.model = route.model

    def analyze_with_context(
        self,
        *,
        audio_path: Path,
        workspace_id: object | None,
        project_id: object | None,
    ) -> dict[str, object]:
        result = self.gateway.invoke(
            AudioIntelligenceRequest(audio_path=audio_path),
            context=ModelCallContext(
                workspace_id=workspace_id,
                project_id=project_id,
            ),
        )
        if not isinstance(result.output, AudioIntelligenceOutput):
            raise TypeError("audio intelligence route returned the wrong typed output")
        return dict(result.output.insights)


class LocalClipEmbeddingsAdapter:
    def __init__(self, provider: object) -> None:
        self.provider = provider

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        del route
        if isinstance(request, ImageEmbeddingsRequest):
            hints = request.source_hints or (None,) * len(request.images)
            vectors = tuple(
                tuple(
                    self.provider.embed_image(
                        image_bytes=image,
                        source_hint=hint,
                    )
                )
                for image, hint in zip(request.images, hints, strict=True)
            )
            return AdapterResult(
                output=EmbeddingsOutput(vectors=vectors, raw_response={}),
                usage=ModelUsage(input_units=len(request.images)),
            )
        from app.services.model_gateway import EmbeddingsRequest

        if isinstance(request, EmbeddingsRequest):
            vectors = tuple(tuple(self.provider.embed_text(text)) for text in request.inputs)
            return AdapterResult(
                output=EmbeddingsOutput(vectors=vectors, raw_response={}),
                usage=ModelUsage(input_units=len(request.inputs)),
            )
        raise TypeError("CLIP adapter requires text or image embeddings request")


class GatewayClipEmbeddingProvider:
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        text_operation: ModelOperation = ModelOperation.EMBEDDINGS_TEXT,
    ) -> None:
        self.gateway = gateway
        self.text_operation = text_operation
        embedding_routes = [
            route
            for route in gateway.routes
            if route.capability == ModelCapability.EMBEDDINGS
        ]
        route_identity = "|".join(
            f"{route.route_id}:{route.model_revision}" for route in embedding_routes
        )
        self.cache_version = f"gateway:{hashlib.sha256(route_identity.encode()).hexdigest()}"
        self.preferred_model_revision = (
            embedding_routes[0].model_revision if embedding_routes else None
        )
        self._model_version: ContextVar[str] = ContextVar(
            "gateway_clip_model_version",
            default="gateway-unresolved",
        )

    @property
    def model_version(self) -> str:
        return self._model_version.get()

    def embed_text(
        self,
        text: str,
        *,
        workspace_id: object | None = None,
        project_id: object | None = None,
    ) -> list[float]:
        if not text.strip():
            return []
        from app.services.model_gateway import EmbeddingsRequest

        result = self.gateway.invoke(
            EmbeddingsRequest(inputs=(text,)),
            context=ModelCallContext(workspace_id=workspace_id, project_id=project_id),
        )
        if not isinstance(result.output, EmbeddingsOutput) or not result.output.vectors:
            raise TypeError("text embeddings route returned the wrong typed output")
        self._model_version.set(result.metadata.model_revision)
        return list(result.output.vectors[0])

    def embed_image(
        self,
        *,
        image_bytes: bytes,
        source_hint: str | None = None,
        workspace_id: object | None = None,
        project_id: object | None = None,
    ) -> list[float]:
        result = self.gateway.invoke(
            ImageEmbeddingsRequest(
                images=(image_bytes,),
                source_hints=(source_hint,),
            ),
            context=ModelCallContext(workspace_id=workspace_id, project_id=project_id),
        )
        if not isinstance(result.output, EmbeddingsOutput) or not result.output.vectors:
            raise TypeError("image embeddings route returned the wrong typed output")
        self._model_version.set(result.metadata.model_revision)
        return list(result.output.vectors[0])


class LocalMatteSegmentationAdapter:
    def __init__(self, provider_models: Mapping[str, object]) -> None:
        self.provider_models = dict(provider_models)

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        del route
        if not isinstance(request, MatteSegmentationRequest):
            raise TypeError("matte adapter requires MatteSegmentationRequest")
        required = {"cv2", "np", "selfie_segmentation"}
        if not required.issubset(self.provider_models):
            raise RuntimeError("matte segmentation models are not loaded")
        from app.services.segmentation_matte import generate_selfie_segmentation_matte

        stats = generate_selfie_segmentation_matte(
            cv2=self.provider_models["cv2"],
            np=self.provider_models["np"],
            selfie_segmentation=self.provider_models["selfie_segmentation"],
            source_path=request.source_path,
            output_path=request.output_path,
            max_dimension=request.max_dimension,
        )
        return AdapterResult(
            output=MatteSegmentationOutput(
                fps=float(stats["fps"]),
                width=int(stats["width"]),
                height=int(stats["height"]),
                frame_count=int(stats["frame_count"]),
            ),
            usage=ModelUsage(input_units=max(1, request.source_path.stat().st_size // 1024)),
        )


class LocalStemSeparationAdapter:
    def __init__(self, separator: object) -> None:
        self.separator = separator

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        del route
        if not isinstance(request, StemSeparationRequest):
            raise TypeError("stem adapter requires StemSeparationRequest")
        stems = self.separator.separate(
            source_path=request.source_path,
            model=request.model,
        )
        return AdapterResult(
            output=StemSeparationOutput(stems=dict(stems)),
            usage=ModelUsage(input_units=max(1, request.source_path.stat().st_size // 1024)),
        )


class RunwayVideoGenerationAdapter:
    def __init__(self, provider: object) -> None:
        self.provider = provider

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        provider_model = getattr(self.provider, "model", route.model)
        provider_api_version = getattr(self.provider, "api_version", None)
        if provider_model != route.model:
            raise ValueError("Runway route model does not match transport model")
        if provider_api_version and provider_api_version not in route.model_revision:
            raise ValueError("Runway route revision does not match transport API version")
        if isinstance(request, VideoGenerationSubmitRequest):
            if not request.prompt_image_url:
                raise ProviderCallFailure(
                    provider="runway",
                    error_info=ProviderErrorInfo(
                        "provider_request_rejected",
                        False,
                        fallback_eligible=False,
                        circuit_worthy=False,
                        billable=False,
                    ),
                    message="Runway video generation requires a prompt image.",
                )
            import inspect

            submit_values = {
                "prompt": request.prompt,
                "prompt_image_url": request.prompt_image_url,
                "duration_s": request.duration_s,
                "ratio": request.ratio,
            }
            if "idempotency_key" in inspect.signature(
                self.provider.initiate_generation
            ).parameters:
                submit_values["idempotency_key"] = request.idempotency_key
            operation_id = self.provider.initiate_generation(**submit_values)
            return AdapterResult(
                output=GenerationOperationOutput(
                    operation_id=operation_id,
                    raw_response={"route_id": route.route_id},
                ),
                usage=ModelUsage(output_units=request.duration_s),
            )
        if isinstance(request, VideoGenerationPollRequest):
            video_url = self.provider.poll_task(request.operation_id)
            return AdapterResult(
                output=GenerationOutput(
                    artifacts=(GeneratedArtifact(url=video_url),),
                    operation_id=request.operation_id,
                    raw_response={},
                ),
                usage=ModelUsage(),
            )
        raise TypeError("Runway adapter requires video submit or poll request")


def bundle_revision(*parts: object) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()
    return f"sha256:{digest}"


def register_visual_route(
    gateway: ModelGateway,
    *,
    provider: object,
    model: str,
    revision: str,
    route_id: str = "local-visual-manifest",
) -> ModelRoute:
    adapter_name = f"{route_id}-adapter"
    route = ModelRoute(
        route_id=route_id,
        capability=ModelCapability.VISION,
        adapter=adapter_name,
        provider=getattr(provider, "provider_name", "local_visual"),
        model=model,
        model_revision=revision,
        operations=(ModelOperation.VISION_VIDEO_MANIFEST,),
        priority=10,
    )
    gateway.register_adapter(adapter_name, LocalVisualManifestAdapter(provider))
    gateway.register_route(route)
    return route


def register_audio_route(
    gateway: ModelGateway,
    *,
    providers: Mapping[str, object],
    revision: str,
) -> ModelRoute:
    adapter_name = "local-audio-intelligence-adapter"
    route = ModelRoute(
        route_id="local-audio-intelligence",
        capability=ModelCapability.AUDIO,
        adapter=adapter_name,
        provider="local_audio_ml",
        model="pyannote|wav2vec2|yamnet",
        model_revision=revision,
        operations=(ModelOperation.AUDIO_INTELLIGENCE,),
        priority=10,
    )
    gateway.register_adapter(adapter_name, LocalAudioIntelligenceAdapter(providers))
    gateway.register_route(route)
    return route


def register_clip_routes(
    gateway: ModelGateway,
    *,
    primary_provider: object,
    primary_revision: str,
    fallback_provider: object | None,
    fallback_revision: str = "compatible-hash-clip-v1",
) -> tuple[ModelRoute, ModelRoute | None]:
    """Registers local CLIP and, outside production, the hash-embedding test
    fallback (fixtures/registry.json: stock-embeddings). Pass
    ``fallback_provider=None`` to register no fallback."""
    operations = (ModelOperation.EMBEDDINGS_TEXT, ModelOperation.EMBEDDINGS_IMAGE)
    primary = ModelRoute(
        route_id="local-clip-primary",
        capability=ModelCapability.EMBEDDINGS,
        adapter="local-clip-primary-adapter",
        provider="local_clip",
        model=getattr(primary_provider, "model_name", "clip"),
        model_revision=primary_revision,
        operations=operations,
        priority=10,
    )
    fallback = ModelRoute(
        route_id="compatible-hash-clip-fallback",
        capability=ModelCapability.EMBEDDINGS,
        adapter="compatible-hash-clip-adapter",
        provider="compatible_hash_clip",
        model="compatible-hash-clip",
        model_revision=fallback_revision,
        operations=operations,
        priority=1000,
    )
    gateway.register_adapter(
        primary.adapter,
        LocalClipEmbeddingsAdapter(primary_provider),
    )
    gateway.register_route(primary)
    if fallback_provider is None:
        return primary, None
    gateway.register_adapter(
        fallback.adapter,
        LocalClipEmbeddingsAdapter(fallback_provider),
    )
    gateway.register_route(fallback)
    return primary, fallback


def register_matte_route(
    gateway: ModelGateway,
    *,
    provider_models: Mapping[str, object],
    revision: str,
) -> ModelRoute:
    route = ModelRoute(
        route_id="local-matte-segmentation",
        capability=ModelCapability.VISION,
        adapter="local-matte-segmentation-adapter",
        provider="local_mediapipe",
        model="mediapipe-selfie-segmentation",
        model_revision=revision,
        operations=(ModelOperation.VISION_MATTE_SEGMENTATION,),
        priority=10,
    )
    gateway.register_adapter(route.adapter, LocalMatteSegmentationAdapter(provider_models))
    gateway.register_route(route)
    return route


def register_stem_route(
    gateway: ModelGateway,
    *,
    separator: object,
    model: str,
    revision: str,
) -> ModelRoute:
    route = ModelRoute(
        route_id="local-demucs-stem-separation",
        capability=ModelCapability.AUDIO,
        adapter="local-demucs-stem-separation-adapter",
        provider="local_demucs",
        model=model,
        model_revision=revision,
        operations=(ModelOperation.AUDIO_STEM_SEPARATION,),
        priority=10,
    )
    gateway.register_adapter(route.adapter, LocalStemSeparationAdapter(separator))
    gateway.register_route(route)
    return route


def register_runway_route(
    gateway: ModelGateway,
    *,
    provider: object,
    model: str,
    revision: str,
    fixed_cost_usd: float,
    endpoint_host: str | None = None,
    requires_live_effects_gate: bool = False,
) -> ModelRoute:
    route = ModelRoute(
        route_id="runway-video-generation",
        capability=ModelCapability.GENERATION,
        adapter="runway-video-generation-adapter",
        provider="runway",
        model=model,
        model_revision=revision,
        operations=(
            ModelOperation.GENERATION_VIDEO_SUBMIT,
            ModelOperation.GENERATION_VIDEO_POLL,
        ),
        priority=10,
        fixed_cost_usd=fixed_cost_usd,
        endpoint_host=endpoint_host,
        requires_live_effects_gate=requires_live_effects_gate,
    )
    gateway.register_adapter(route.adapter, RunwayVideoGenerationAdapter(provider))
    gateway.register_route(route)
    return route
