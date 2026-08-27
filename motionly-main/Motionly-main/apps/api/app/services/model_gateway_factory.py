from __future__ import annotations

import json
from collections.abc import Mapping

from app.services.model_adapters import AzureOpenAIAdapter, OpenAICompatibleAdapter
from app.services.model_gateway import (
    InMemoryBudgetLedger,
    InMemoryCircuitStore,
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelRoute,
    OperationsGatewayTelemetry,
    RedisBudgetLedger,
    RedisCircuitStore,
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_HTTP_ADAPTER_OPERATIONS = {
    ModelCapability.PLANNING: ModelOperation.PLANNING_CHAT,
    ModelCapability.TRANSCRIPTION: ModelOperation.TRANSCRIPTION_SPEECH_TO_TEXT,
    ModelCapability.VISION: ModelOperation.VISION_PROMPT,
    ModelCapability.AUDIO: ModelOperation.AUDIO_PROMPT,
    ModelCapability.EMBEDDINGS: ModelOperation.EMBEDDINGS_TEXT,
    ModelCapability.GENERATION: ModelOperation.GENERATION_IMAGE,
}
_HTTP_ADAPTERS = {"azure", "openai_compatible", "openrouter", "groq"}


def model_gateway_from_settings(
    settings: object,
    *,
    metrics_registry: object | None = None,
    operations_repository: object | None = None,
) -> ModelGateway | None:
    if not bool(getattr(settings, "model_gateway_enabled", True)):
        return None

    adapters: dict[str, object] = {}
    openrouter_key = getattr(settings, "openrouter_api_key", None)
    groq_key = getattr(settings, "groq_api_key", None)
    if openrouter_key:
        adapters["openrouter"] = OpenAICompatibleAdapter(
            base_url=OPENROUTER_BASE_URL,
            api_key=str(openrouter_key),
        )
    if groq_key:
        adapters["groq"] = OpenAICompatibleAdapter(
            base_url=GROQ_BASE_URL,
            api_key=str(groq_key),
        )

    custom_base_url = getattr(settings, "model_gateway_openai_base_url", None)
    if custom_base_url:
        adapters["openai_compatible"] = OpenAICompatibleAdapter(
            base_url=str(custom_base_url),
            api_key=getattr(settings, "model_gateway_openai_api_key", None),
            allow_insecure_http=str(getattr(settings, "app_env", "production"))
            in {"local", "development"},
        )

    azure_endpoint = getattr(settings, "azure_openai_endpoint", None)
    if azure_endpoint:
        adapters["azure"] = AzureOpenAIAdapter(
            base_url=str(azure_endpoint),
            api_key=getattr(settings, "azure_openai_api_key", None),
            api_version=str(
                getattr(settings, "azure_openai_api_version", "2025-01-01-preview")
            ),
        )

    configured_routes = str(getattr(settings, "model_gateway_routes", "") or "").strip()
    routes = (
        _parse_routes(configured_routes)
        if configured_routes
        else _legacy_routes(settings, adapters=adapters)
    )
    redis_url = getattr(settings, "redis_url", None)
    failure_threshold = int(getattr(settings, "model_gateway_failure_threshold", 3))
    cooldown_seconds = int(getattr(settings, "model_gateway_circuit_cooldown_seconds", 60))
    if redis_url:
        budget_ledger = RedisBudgetLedger(str(redis_url))
        circuit_store = RedisCircuitStore(
            str(redis_url),
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )
    else:
        budget_ledger = InMemoryBudgetLedger()
        circuit_store = InMemoryCircuitStore(
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )

    return ModelGateway(
        adapters=adapters,  # type: ignore[arg-type]
        routes=routes,
        budget_ledger=budget_ledger,
        circuit_store=circuit_store,
        telemetry=OperationsGatewayTelemetry(
            metrics_registry=metrics_registry,
            operations_repository=operations_repository,
        ),
        monthly_budget_usd=float(
            getattr(settings, "model_gateway_monthly_budget_usd", 100.0)
        ),
        request_budget_usd=float(
            getattr(settings, "model_gateway_request_budget_usd", 5.0)
        ),
    )


def _legacy_routes(settings: object, *, adapters: Mapping[str, object]) -> list[ModelRoute]:
    routes: list[ModelRoute] = []
    planner_provider = str(getattr(settings, "edit_planner_provider", "")).strip().lower()
    planner_model = str(
        getattr(settings, "edit_planner_model", "openai/gpt-4.1-mini")
    )
    if "openrouter" in adapters and planner_provider in {"openrouter", "openai_compatible"}:
        free_model = planner_model == "openrouter/free" or planner_model.endswith(":free")
        routes.append(
            ModelRoute(
                route_id="openrouter-planning-primary",
                capability=ModelCapability.PLANNING,
                adapter="openrouter",
                provider="openrouter",
                model=planner_model,
                model_revision=str(
                    getattr(settings, "edit_planner_model_revision", "")
                    or "openai/gpt-4.1-mini-2025-04-14"
                ),
                operations=(ModelOperation.PLANNING_CHAT,),
                priority=100,
                explicitly_free=free_model,
                input_cost_per_million_usd=(
                    0.0
                    if free_model
                    else float(getattr(settings, "edit_planner_input_cost_per_million_usd", 2.0))
                ),
                output_cost_per_million_usd=(
                    0.0
                    if free_model
                    else float(
                        getattr(settings, "edit_planner_output_cost_per_million_usd", 2.0)
                    )
                ),
            )
        )

    transcription_provider = str(
        getattr(settings, "transcription_provider", "")
    ).strip().lower()
    transcription_model = str(
        getattr(settings, "transcription_model", "whisper-large-v3-turbo")
    )
    if "groq" in adapters and transcription_provider in {"groq", "openai_compatible"}:
        routes.append(
            ModelRoute(
                route_id="groq-transcription-primary",
                capability=ModelCapability.TRANSCRIPTION,
                adapter="groq",
                provider="groq",
                model=transcription_model,
                model_revision=str(
                    getattr(settings, "transcription_model_revision", "")
                    or "whisper-large-v3-turbo-2025-02-01"
                ),
                operations=(ModelOperation.TRANSCRIPTION_SPEECH_TO_TEXT,),
                priority=100,
                fixed_cost_usd=float(
                    getattr(settings, "transcription_estimated_cost_per_request_usd", 0.25)
                ),
                max_output_units=1,
            )
        )
    return routes


def _parse_routes(value: str) -> list[ModelRoute]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("MODEL_GATEWAY_ROUTES must be valid JSON") from exc
    if isinstance(payload, Mapping):
        payload = payload.get("routes")
    if not isinstance(payload, list):
        raise ValueError("MODEL_GATEWAY_ROUTES must be a JSON array or an object with routes")

    routes: list[ModelRoute] = []
    route_ids: set[str] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, Mapping):
            raise ValueError(f"MODEL_GATEWAY_ROUTES item {index} must be an object")
        route_id = str(item.get("route_id") or "")
        if route_id in route_ids:
            raise ValueError(f"MODEL_GATEWAY_ROUTES contains duplicate route_id {route_id!r}")
        route_ids.add(route_id)
        try:
            capability = ModelCapability(str(item["capability"]))
            for boolean_field in ("explicitly_free", "enabled"):
                if boolean_field in item and not isinstance(item[boolean_field], bool):
                    raise ValueError(f"{boolean_field} must be a JSON boolean")
            adapter = str(item["adapter"])
            raw_operations = item.get("operations") or []
            operations = tuple(ModelOperation(str(operation)) for operation in raw_operations)
            if adapter in _HTTP_ADAPTERS:
                supported_operation = _HTTP_ADAPTER_OPERATIONS[capability]
                if operations and operations != (supported_operation,):
                    raise ValueError(
                        f"adapter {adapter!r} supports only {supported_operation.value!r} "
                        f"for {capability.value}"
                    )
                operations = (supported_operation,)
            route = ModelRoute(
                route_id=route_id,
                capability=capability,
                adapter=adapter,
                provider=str(item["provider"]),
                model=str(item["model"]),
                model_revision=str(item["model_revision"]),
                operations=operations,
                priority=int(item.get("priority", 100)),
                input_cost_per_million_usd=float(
                    item.get("input_cost_per_million_usd", 0.0)
                ),
                output_cost_per_million_usd=float(
                    item.get("output_cost_per_million_usd", 0.0)
                ),
                fixed_cost_usd=float(item.get("fixed_cost_usd", 0.0)),
                explicitly_free=bool(item.get("explicitly_free", False)),
                max_output_units=int(item.get("max_output_units", 4096)),
                enabled=bool(item.get("enabled", True)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"MODEL_GATEWAY_ROUTES item {index} is invalid: {exc}") from exc
        routes.append(route)
    return routes
