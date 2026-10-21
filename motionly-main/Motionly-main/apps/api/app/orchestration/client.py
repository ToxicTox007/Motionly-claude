from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from temporalio.client import Client, TLSConfig
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.service import RPCError, RPCStatusCode

from app.orchestration.contracts import (
    ApprovalGateSignal,
    StepOutcomeSignal,
    TemporalExecutionDescription,
    TemporalStartResult,
    WorkflowStartInput,
    assert_identifier_only_history,
)

if TYPE_CHECKING:
    from app.core.config import Settings


@dataclass(frozen=True)
class TemporalConnectionConfig:
    target_host: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = "motionly-orchestration"
    api_key: str | None = field(default=None, repr=False)
    tls_enabled: bool = False
    server_root_ca_path: str | None = None
    client_cert_path: str | None = None
    client_private_key_path: str | None = field(default=None, repr=False)
    server_name: str | None = None
    deployment_mode: str = "cloud"
    identity: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> TemporalConnectionConfig:
        return cls(
            target_host=str(settings.temporal_address or "localhost:7233").strip(),
            namespace=str(settings.temporal_namespace or "default").strip(),
            task_queue=str(settings.temporal_task_queue or "motionly-orchestration").strip(),
            api_key=settings.temporal_api_key,
            tls_enabled=settings.temporal_tls_enabled,
            server_root_ca_path=settings.temporal_server_root_ca_path,
            client_cert_path=settings.temporal_client_cert_path,
            client_private_key_path=settings.temporal_client_private_key_path,
            server_name=settings.temporal_server_name,
            deployment_mode=settings.temporal_deployment_mode,
            identity=settings.temporal_identity,
        )

    @classmethod
    def from_env(cls) -> TemporalConnectionConfig:
        tls_value = os.getenv("TEMPORAL_TLS_ENABLED", "false")
        return cls(
            target_host=os.getenv("TEMPORAL_ADDRESS", "localhost:7233").strip(),
            namespace=os.getenv("TEMPORAL_NAMESPACE", "default").strip(),
            task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "motionly-orchestration").strip(),
            api_key=_secret_from_env("TEMPORAL_API_KEY"),
            tls_enabled=tls_value.lower() in {"1", "true", "yes", "on"},
            server_root_ca_path=os.getenv("TEMPORAL_SERVER_ROOT_CA_PATH"),
            client_cert_path=os.getenv("TEMPORAL_CLIENT_CERT_PATH"),
            client_private_key_path=os.getenv("TEMPORAL_CLIENT_PRIVATE_KEY_PATH"),
            server_name=os.getenv("TEMPORAL_SERVER_NAME") or None,
            deployment_mode=os.getenv("TEMPORAL_DEPLOYMENT_MODE", "cloud"),
            identity=os.getenv("TEMPORAL_IDENTITY") or None,
        )

    def validate(self) -> None:
        if not self.target_host or ":" not in self.target_host:
            raise ValueError("TEMPORAL_ADDRESS must include host and port")
        if not self.namespace or not self.task_queue:
            raise ValueError("Temporal namespace and task queue are required")
        if bool(self.client_cert_path) != bool(self.client_private_key_path):
            raise ValueError(
                "Temporal mTLS certificate and private key must be configured together"
            )
        if (self.client_cert_path or self.server_root_ca_path) and not self.tls_enabled:
            raise ValueError("Temporal certificate paths require TLS")
        if self.deployment_mode.strip().lower() == "self_hosted":
            missing = [
                name
                for name, value in {
                    "TEMPORAL_TLS_ENABLED": self.tls_enabled,
                    "TEMPORAL_SERVER_ROOT_CA_PATH": self.server_root_ca_path,
                    "TEMPORAL_CLIENT_CERT_PATH": self.client_cert_path,
                    "TEMPORAL_CLIENT_PRIVATE_KEY_PATH": self.client_private_key_path,
                    "TEMPORAL_SERVER_NAME": self.server_name,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError(
                    "Self-hosted Temporal requires mTLS configuration: " + ", ".join(missing)
                )


async def connect_temporal_client(config: TemporalConnectionConfig | None = None) -> Client:
    config = config or TemporalConnectionConfig.from_env()
    config.validate()
    tls: bool | TLSConfig | None = False
    if config.tls_enabled:
        tls = TLSConfig(
            server_root_ca_cert=_read_optional(config.server_root_ca_path),
            client_cert=_read_optional(config.client_cert_path),
            client_private_key=_read_optional(config.client_private_key_path),
            verification_server_name=config.server_name,
        )
    return await Client.connect(
        config.target_host,
        namespace=config.namespace,
        api_key=config.api_key,
        tls=tls,
        identity=config.identity,
    )


class TemporalOrchestrationClient:
    def __init__(self, client: Client, *, task_queue: str) -> None:
        if not task_queue:
            raise ValueError("Temporal task queue is required")
        self.client = client
        self.task_queue = task_queue

    async def start_workflow(
        self,
        *,
        workflow_id: str,
        input: WorkflowStartInput,
    ) -> TemporalStartResult:
        from app.orchestration.workflow import MotionlyWorkflow

        if input.temporal_workflow_id != workflow_id:
            raise ValueError("Temporal workflow ID must match the canonical run")
        assert_identifier_only_history(input)
        handle = await self.client.start_workflow(
            MotionlyWorkflow.run,
            input,
            id=workflow_id,
            task_queue=self.task_queue,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            execution_timeout=timedelta(days=30),
            task_timeout=timedelta(seconds=20),
            request_id=f"start:{input.workflow_run_id}",
        )
        run_id = handle.result_run_id or handle.run_id
        if not run_id:
            description = await handle.describe()
            run_id = description.run_id
        if not run_id:
            raise RuntimeError("Temporal start did not return a run ID")
        return TemporalStartResult(workflow_id=handle.id, run_id=run_id)

    async def signal_workflow(
        self,
        *,
        workflow_id: str,
        signal: StepOutcomeSignal | ApprovalGateSignal,
    ) -> str:
        assert_identifier_only_history(signal)
        handle = self.client.get_workflow_handle(workflow_id)
        await handle.signal(
            "approval_gate" if isinstance(signal, ApprovalGateSignal) else "step_outcome",
            signal,
        )
        return f"signal:{signal.event_id}"

    async def describe_workflow(self, *, workflow_id: str) -> TemporalExecutionDescription | None:
        handle = self.client.get_workflow_handle(workflow_id)
        try:
            description = await handle.describe()
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return None
            raise
        return TemporalExecutionDescription(
            workflow_id=workflow_id,
            run_id=description.run_id,
            status=description.status.name.lower(),
        )

    async def cancel_workflow(self, *, workflow_id: str, reason: str) -> bool:
        """Cancel an orphaned workflow by id, idempotently.

        A workflow that Temporal no longer knows about (already closed, or never
        started because its project was deleted before dispatch) is treated as a
        terminal, quiet success: NOT_FOUND means the desired end state already
        holds. Returns True when a running execution was cancelled, False when
        Temporal had nothing to cancel.
        """
        handle = self.client.get_workflow_handle(workflow_id)
        try:
            await handle.cancel()
        except RPCError as exc:
            if exc.status in (RPCStatusCode.NOT_FOUND, RPCStatusCode.FAILED_PRECONDITION):
                return False
            raise
        return True


def _read_optional(path: str | None) -> bytes | None:
    if not path:
        return None
    return Path(path).read_bytes()


def _secret_from_env(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value.strip()
    file_path = os.getenv(f"{name}_FILE")
    if not file_path:
        return None
    return Path(file_path).read_text(encoding="utf-8").strip()
