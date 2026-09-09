"""Fail-closed authorization for Motionly's isolated local-audit runtime.

The gate is intentionally independent of framework configuration so the plan-only
capability preflight and runtime boundaries apply the same validation rules.  It
never stores or returns authorization values, account identifiers, secret paths,
or provider URLs with credentials.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

LOCAL_AUDIT_AUTHORIZATION_SECRET_NAME = "local_audit_external_authorization"
LOCAL_AUDIT_AUTHORIZATION_MOUNT = (
    f"/run/secrets/{LOCAL_AUDIT_AUTHORIZATION_SECRET_NAME}"
)
_ALLOWED_AUDIT_TARGETS = frozenset({"local-sandbox", "staging-sandbox"})
_ALLOWED_PUBLISH_VISIBILITIES = frozenset({"private", "unlisted"})
_PRODUCTION_MOTIONLY_HOSTS = frozenset({"motionly.com", "motionly.ai"})
_ACCOUNT_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,254}$")
_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_RUN_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_NON_PRODUCTION_LABEL_PATTERN = re.compile(
    r"(?:^|[-_.:])(audit|sandbox|staging|test|dev|nonprod)(?:$|[-_.:])",
    re.IGNORECASE,
)
_PRODUCTION_LABEL_PATTERN = re.compile(
    r"(?:^|[-_.:])(prod|production|live)(?:$|[-_.:])",
    re.IGNORECASE,
)
_PLACEHOLDER_MARKERS = (
    "<",
    ">",
    "changeme",
    "change-me",
    "replace",
    "placeholder",
    "example",
    "your-",
    "todo",
)

# Publishing reaches these exact hosts after the gate.  The YouTube refresh
# endpoint is distinct from the upload endpoint, so both must be allowlisted.
PUBLISHING_REQUIRED_HOSTS = {
    "tiktok": frozenset({"open.tiktokapis.com"}),
    "instagram": frozenset({"graph.facebook.com"}),
    "youtube": frozenset({"www.googleapis.com", "oauth2.googleapis.com"}),
}


class LocalAuditEffectsDenied(RuntimeError):
    """Raised before a local-audit runtime could make a remote side effect."""

    code = "local_audit_live_effects_denied"

    def __init__(self, reason: str) -> None:
        super().__init__("Local-audit live effects are blocked.")
        self.reason = reason


@dataclass(frozen=True)
class AuditAccount:
    provider: str
    account_id: str

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.account_id)


@dataclass(frozen=True)
class LocalAuditPolicy:
    target: str
    run_id_fingerprint: str
    authorization_fingerprint: str
    external_effects_enabled: bool
    provider_accounts: tuple[AuditAccount, ...]
    publishing_accounts: tuple[AuditAccount, ...]
    approved_domains: frozenset[str]
    allowed_publish_visibilities: frozenset[str]
    max_per_run_spend_usd: float
    max_total_spend_usd: float
    cleanup_owner_fingerprint: str
    cleanup_reference_fingerprint: str
    cleanup_policy: str

    def safe_report(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "target": self.target,
            "run_id_hash": self.run_id_fingerprint,
            "external_effects_enabled": self.external_effects_enabled,
            "authorization": {
                "present": True,
                "fingerprint": self.authorization_fingerprint,
            },
            "provider_accounts": [
                {"provider": account.provider, "account_hash": account.fingerprint}
                for account in self.provider_accounts
            ],
            "publishing_accounts": [
                {"platform": account.provider, "account_hash": account.fingerprint}
                for account in self.publishing_accounts
            ],
            "approved_domains": sorted(self.approved_domains),
            "allowed_publish_visibilities": sorted(self.allowed_publish_visibilities),
            "spend_controls": {
                "max_per_run_usd": _round_currency(self.max_per_run_spend_usd),
                "max_total_usd": _round_currency(self.max_total_spend_usd),
            },
            "remote_cleanup": {
                "owner_hash": self.cleanup_owner_fingerprint,
                "reference_hash": self.cleanup_reference_fingerprint,
                "policy": self.cleanup_policy,
            },
        }


@dataclass(frozen=True)
class PolicyCompilation:
    policy: LocalAuditPolicy | None
    errors: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return self.policy is not None and not self.errors


@dataclass(frozen=True)
class LocalAuditDecision:
    action: str
    allowed: bool
    reason: str
    provider: str | None = None
    endpoint_hosts: tuple[str, ...] = ()
    account_hash: str | None = None
    estimated_cost_usd: float | None = None

    def as_report(self) -> dict[str, object]:
        report: dict[str, object] = {
            "action": self.action,
            "allowed": self.allowed,
            "reason": self.reason,
        }
        if self.provider:
            report["provider"] = self.provider
        if self.endpoint_hosts:
            report["endpoint_hosts"] = list(self.endpoint_hosts)
        if self.account_hash:
            report["account_hash"] = self.account_hash
        if self.estimated_cost_usd is not None:
            report["estimated_cost_usd"] = _round_currency(self.estimated_cost_usd)
        return report


class InMemoryLocalAuditSpendLedger:
    """Conservative per-process spend reservations for one audit run.

    A reservation is deliberately retained after a model adapter is attempted:
    a timeout or provider error can still be billable, so releasing it would
    make the configured total ceiling bypassable through retries.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reserved_usd = 0.0
        self._reservation_count = 0

    def reserve(
        self,
        *,
        amount_usd: float,
        max_per_run_usd: float,
        max_total_usd: float,
    ) -> None:
        if not math.isfinite(amount_usd) or amount_usd < 0:
            raise LocalAuditEffectsDenied("invalid_estimated_cost")
        if amount_usd > max_per_run_usd + 1e-9:
            raise LocalAuditEffectsDenied("per_run_spend_ceiling_exceeded")
        with self._lock:
            if self._reserved_usd + amount_usd > max_total_usd + 1e-9:
                raise LocalAuditEffectsDenied("aggregate_spend_ceiling_exceeded")
            self._reserved_usd += amount_usd
            self._reservation_count += 1

    def safe_report(self) -> dict[str, object]:
        with self._lock:
            return {
                "reserved_usd": _round_currency(self._reserved_usd),
                "reservation_count": self._reservation_count,
            }


def compile_local_audit_policy(
    values: Mapping[str, object],
    *,
    authorization: str | None = None,
    authorization_file: str | None = None,
    require_runtime_authorization_mount: bool = False,
) -> PolicyCompilation:
    """Validate untrusted audit controls without exposing their raw values."""

    errors: list[str] = []
    if not _as_bool(values.get("LOCAL_AUDIT_MODE")):
        errors.append("local_audit_mode_required")

    target = _required_string(values.get("LOCAL_AUDIT_TARGET"), "target_required", errors)
    if target and target not in _ALLOWED_AUDIT_TARGETS:
        errors.append("target_must_be_sandbox")

    run_id = _required_string(values.get("LOCAL_AUDIT_RUN_ID"), "run_id_required", errors)
    if run_id and not _RUN_IDENTIFIER_PATTERN.fullmatch(run_id):
        errors.append("run_id_invalid")

    auth_value = authorization
    if auth_value is None:
        raw_authorization = values.get("LOCAL_AUDIT_EXTERNAL_AUTHORIZATION")
        auth_value = str(raw_authorization) if raw_authorization is not None else None
    if _is_placeholder(auth_value, min_length=16):
        errors.append("external_authorization_missing_or_placeholder")
    if (
        require_runtime_authorization_mount
        and authorization_file != LOCAL_AUDIT_AUTHORIZATION_MOUNT
    ):
        errors.append("external_authorization_file_mount_invalid")

    provider_accounts = _parse_accounts(
        values.get("LOCAL_AUDIT_PROVIDER_ACCOUNTS"),
        field="provider_accounts",
        errors=errors,
    )
    publishing_accounts = _parse_accounts(
        values.get("LOCAL_AUDIT_PUBLISHING_ACCOUNTS"),
        field="publishing_accounts",
        errors=errors,
    )
    approved_domains = _parse_domains(values.get("LOCAL_AUDIT_APPROVED_DOMAINS"), errors)
    allowed_visibilities = _parse_visibilities(
        values.get("LOCAL_AUDIT_ALLOWED_PUBLISH_VISIBILITIES"), errors
    )

    max_per_run = _parse_positive_amount(
        values.get("LOCAL_AUDIT_MAX_PER_RUN_SPEND_USD"),
        "max_per_run_spend_required",
        errors,
    )
    max_total = _parse_positive_amount(
        values.get("LOCAL_AUDIT_MAX_TOTAL_SPEND_USD"),
        "max_total_spend_required",
        errors,
    )
    if max_per_run is not None and max_total is not None and max_per_run > max_total:
        errors.append("per_run_spend_exceeds_total_spend")

    cleanup_owner = _required_string(
        values.get("LOCAL_AUDIT_REMOTE_CLEANUP_OWNER"),
        "remote_cleanup_owner_required",
        errors,
    )
    cleanup_reference = _required_string(
        values.get("LOCAL_AUDIT_REMOTE_CLEANUP_REFERENCE"),
        "remote_cleanup_reference_required",
        errors,
    )
    cleanup_policy = _required_string(
        values.get("LOCAL_AUDIT_REMOTE_CLEANUP_POLICY"),
        "remote_cleanup_policy_required",
        errors,
    )
    if cleanup_policy and "delete" not in cleanup_policy.lower():
        errors.append("remote_cleanup_policy_must_delete_artifacts")

    if errors:
        return PolicyCompilation(policy=None, errors=tuple(sorted(set(errors))))

    assert target is not None
    assert run_id is not None
    assert auth_value is not None
    assert max_per_run is not None
    assert max_total is not None
    assert cleanup_owner is not None
    assert cleanup_reference is not None
    assert cleanup_policy is not None
    return PolicyCompilation(
        policy=LocalAuditPolicy(
            target=target,
            run_id_fingerprint=_fingerprint(run_id),
            authorization_fingerprint=_fingerprint(auth_value),
            external_effects_enabled=_as_bool(
                values.get("LOCAL_AUDIT_EXTERNAL_EFFECTS_ENABLED")
            ),
            provider_accounts=provider_accounts,
            publishing_accounts=publishing_accounts,
            approved_domains=frozenset(approved_domains),
            allowed_publish_visibilities=frozenset(allowed_visibilities),
            max_per_run_spend_usd=max_per_run,
            max_total_spend_usd=max_total,
            cleanup_owner_fingerprint=_fingerprint(cleanup_owner),
            cleanup_reference_fingerprint=_fingerprint(cleanup_reference),
            cleanup_policy=cleanup_policy,
        ),
        errors=(),
    )


class LocalAuditLiveEffectsGate:
    """Authorizes only explicitly configured local-audit remote operations."""

    def __init__(
        self,
        compilation: PolicyCompilation,
        *,
        spend_ledger: InMemoryLocalAuditSpendLedger | None = None,
    ) -> None:
        self._compilation = compilation
        self._spend_ledger = spend_ledger or InMemoryLocalAuditSpendLedger()
        self._decisions: deque[LocalAuditDecision] = deque(maxlen=25)
        self._decision_lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: object) -> LocalAuditLiveEffectsGate | None:
        if not bool(getattr(settings, "local_audit_mode", False)):
            return None
        values = {
            "LOCAL_AUDIT_MODE": getattr(settings, "local_audit_mode", False),
            "LOCAL_AUDIT_TARGET": getattr(settings, "local_audit_target", None),
            "LOCAL_AUDIT_RUN_ID": getattr(settings, "local_audit_run_id", None),
            "LOCAL_AUDIT_EXTERNAL_EFFECTS_ENABLED": getattr(
                settings, "local_audit_external_effects_enabled", False
            ),
            "LOCAL_AUDIT_PROVIDER_ACCOUNTS": getattr(
                settings, "local_audit_provider_accounts", None
            ),
            "LOCAL_AUDIT_PUBLISHING_ACCOUNTS": getattr(
                settings, "local_audit_publishing_accounts", None
            ),
            "LOCAL_AUDIT_APPROVED_DOMAINS": getattr(
                settings, "local_audit_approved_domains", None
            ),
            "LOCAL_AUDIT_ALLOWED_PUBLISH_VISIBILITIES": getattr(
                settings, "local_audit_allowed_publish_visibilities", None
            ),
            "LOCAL_AUDIT_MAX_PER_RUN_SPEND_USD": getattr(
                settings, "local_audit_max_per_run_spend_usd", None
            ),
            "LOCAL_AUDIT_MAX_TOTAL_SPEND_USD": getattr(
                settings, "local_audit_max_total_spend_usd", None
            ),
            "LOCAL_AUDIT_REMOTE_CLEANUP_OWNER": getattr(
                settings, "local_audit_remote_cleanup_owner", None
            ),
            "LOCAL_AUDIT_REMOTE_CLEANUP_REFERENCE": getattr(
                settings, "local_audit_remote_cleanup_reference", None
            ),
            "LOCAL_AUDIT_REMOTE_CLEANUP_POLICY": getattr(
                settings, "local_audit_remote_cleanup_policy", None
            ),
        }
        return cls(
            compile_local_audit_policy(
                values,
                authorization=getattr(settings, "local_audit_external_authorization", None),
                authorization_file=getattr(
                    settings, "local_audit_external_authorization_file", None
                ),
                require_runtime_authorization_mount=True,
            )
        )

    def authorize_model(
        self,
        *,
        provider: str,
        endpoint_host: str | None,
        estimated_cost_usd: float,
        explicitly_free: bool,
    ) -> None:
        provider_key = _normalize_provider(provider)
        host = _normalize_host(endpoint_host)
        policy = self._require_enabled_policy(
            action="model_invoke",
            provider=provider_key,
            endpoint_hosts=(host,) if host else (),
            estimated_cost_usd=estimated_cost_usd,
        )
        account = _find_account(policy.provider_accounts, provider_key)
        if account is None:
            self._deny(
                action="model_invoke",
                reason="provider_account_not_allowlisted",
                provider=provider_key,
                endpoint_hosts=(host,) if host else (),
                estimated_cost_usd=estimated_cost_usd,
            )
        if host is None:
            self._deny(
                action="model_invoke",
                reason="model_endpoint_host_missing_or_insecure",
                provider=provider_key,
                estimated_cost_usd=estimated_cost_usd,
            )
        if host not in policy.approved_domains:
            self._deny(
                action="model_invoke",
                reason="model_endpoint_host_not_approved",
                provider=provider_key,
                endpoint_hosts=(host,),
                account_hash=account.fingerprint,
                estimated_cost_usd=estimated_cost_usd,
            )
        if not explicitly_free and estimated_cost_usd <= 0:
            self._deny(
                action="model_invoke",
                reason="model_route_cost_not_explicit",
                provider=provider_key,
                endpoint_hosts=(host,),
                account_hash=account.fingerprint,
                estimated_cost_usd=estimated_cost_usd,
            )
        try:
            self._spend_ledger.reserve(
                amount_usd=estimated_cost_usd,
                max_per_run_usd=policy.max_per_run_spend_usd,
                max_total_usd=policy.max_total_spend_usd,
            )
        except LocalAuditEffectsDenied as error:
            self._deny(
                action="model_invoke",
                reason=error.reason,
                provider=provider_key,
                endpoint_hosts=(host,),
                account_hash=account.fingerprint,
                estimated_cost_usd=estimated_cost_usd,
            )
        self._record(
            LocalAuditDecision(
                action="model_invoke",
                allowed=True,
                reason="authorized",
                provider=provider_key,
                endpoint_hosts=(host,),
                account_hash=account.fingerprint,
                estimated_cost_usd=estimated_cost_usd,
            )
        )

    def authorize_publish(
        self,
        *,
        platform: str,
        account_id: str,
        privacy: str,
    ) -> None:
        policy, account, hosts = self._authorize_publishing_account(
            action="publish",
            platform=platform,
            account_id=account_id,
        )
        privacy_key = str(privacy).strip().lower()
        if privacy_key not in policy.allowed_publish_visibilities:
            self._deny(
                action="publish",
                reason="publish_visibility_not_approved",
                provider=account.provider,
                endpoint_hosts=hosts,
                account_hash=account.fingerprint,
            )
        self._record(
            LocalAuditDecision(
                action="publish",
                allowed=True,
                reason="authorized",
                provider=account.provider,
                endpoint_hosts=hosts,
                account_hash=account.fingerprint,
            )
        )

    def authorize_publishing_refresh(self, *, platform: str, account_id: str) -> None:
        _policy, account, hosts = self._authorize_publishing_account(
            action="publish_token_refresh",
            platform=platform,
            account_id=account_id,
        )
        self._record(
            LocalAuditDecision(
                action="publish_token_refresh",
                allowed=True,
                reason="authorized",
                provider=account.provider,
                endpoint_hosts=hosts,
                account_hash=account.fingerprint,
            )
        )

    def authorize_oauth_exchange(
        self,
        *,
        platform: str,
        pending_account_id: str | None = None,
    ) -> None:
        """
        Authorize OAuth token exchange before making the network call.
        pending_account_id is used for validation but not yet bound to a stored token.
        """
        platform_key = _normalize_provider(platform)
        hosts = tuple(sorted(PUBLISHING_REQUIRED_HOSTS.get(platform_key, ())))
        policy = self._require_enabled_policy(
            action="oauth_exchange",
            provider=platform_key,
            endpoint_hosts=hosts,
        )
        if not hosts:
            self._deny(
                action="oauth_exchange",
                reason="publishing_platform_not_supported",
                provider=platform_key,
            )
        missing_hosts = [host for host in hosts if host not in policy.approved_domains]
        if missing_hosts:
            self._deny(
                action="oauth_exchange",
                reason="publishing_endpoint_host_not_approved",
                provider=platform_key,
                endpoint_hosts=hosts,
            )
        # If pending_account_id is provided, validate it's allowlisted
        if pending_account_id:
            account = next(
                (
                    item
                    for item in policy.publishing_accounts
                    if item.provider == platform_key and item.account_id == str(pending_account_id)
                ),
                None,
            )
            if account is None:
                self._deny(
                    action="oauth_exchange",
                    reason="publishing_account_not_allowlisted",
                    provider=platform_key,
                    endpoint_hosts=hosts,
                )
        self._record(
            LocalAuditDecision(
                action="oauth_exchange",
                allowed=True,
                reason="authorized",
                provider=platform_key,
                endpoint_hosts=hosts,
            )
        )

    def validate_oauth_account(
        self,
        *,
        platform: str,
        provider_returned_account_id: str,
    ) -> None:
        """
        Validate that the account ID returned by the OAuth provider is allowlisted.
        Call this after token exchange completes.
        """
        platform_key = _normalize_provider(platform)
        hosts = tuple(sorted(PUBLISHING_REQUIRED_HOSTS.get(platform_key, ())))
        policy = self._require_enabled_policy(
            action="oauth_account_validation",
            provider=platform_key,
            endpoint_hosts=hosts,
        )
        account = next(
            (
                item
                for item in policy.publishing_accounts
                if item.provider == platform_key
                and item.account_id == str(provider_returned_account_id)
            ),
            None,
        )
        if account is None:
            self._deny(
                action="oauth_account_validation",
                reason="provider_returned_account_not_allowlisted",
                provider=platform_key,
                endpoint_hosts=hosts,
            )
        self._record(
            LocalAuditDecision(
                action="oauth_account_validation",
                allowed=True,
                reason="authorized",
                provider=platform_key,
                endpoint_hosts=hosts,
                account_hash=account.fingerprint,
            )
        )

    def safe_report(self) -> dict[str, object]:
        if self._compilation.policy is None:
            return {
                "schema_version": 1,
                "status": "blocked",
                "validation_errors": list(self._compilation.errors),
                "spend": self._spend_ledger.safe_report(),
                "recent_decisions": [decision.as_report() for decision in self._decisions],
            }
        report = self._compilation.policy.safe_report()
        report.update(
            {
                "status": (
                    "ready"
                    if self._compilation.policy.external_effects_enabled
                    else "blocked"
                ),
                "spend": self._spend_ledger.safe_report(),
                "recent_decisions": [decision.as_report() for decision in self._decisions],
            }
        )
        return report

    def _authorize_publishing_account(
        self,
        *,
        action: str,
        platform: str,
        account_id: str,
    ) -> tuple[LocalAuditPolicy, AuditAccount, tuple[str, ...]]:
        platform_key = _normalize_provider(platform)
        hosts = tuple(sorted(PUBLISHING_REQUIRED_HOSTS.get(platform_key, ())))
        policy = self._require_enabled_policy(
            action=action,
            provider=platform_key,
            endpoint_hosts=hosts,
        )
        if not hosts:
            self._deny(
                action=action,
                reason="publishing_platform_not_supported",
                provider=platform_key,
            )
        account = next(
            (
                item
                for item in policy.publishing_accounts
                if item.provider == platform_key and item.account_id == str(account_id)
            ),
            None,
        )
        if account is None:
            self._deny(
                action=action,
                reason="publishing_account_not_allowlisted",
                provider=platform_key,
                endpoint_hosts=hosts,
            )
        missing_hosts = [host for host in hosts if host not in policy.approved_domains]
        if missing_hosts:
            self._deny(
                action=action,
                reason="publishing_endpoint_host_not_approved",
                provider=platform_key,
                endpoint_hosts=hosts,
                account_hash=account.fingerprint,
            )
        return policy, account, hosts

    def _require_enabled_policy(
        self,
        *,
        action: str,
        provider: str,
        endpoint_hosts: tuple[str, ...] = (),
        estimated_cost_usd: float | None = None,
    ) -> LocalAuditPolicy:
        policy = self._compilation.policy
        if policy is None:
            self._deny(
                action=action,
                reason="local_audit_policy_incomplete",
                provider=provider,
                endpoint_hosts=endpoint_hosts,
                estimated_cost_usd=estimated_cost_usd,
            )
        if not policy.external_effects_enabled:
            self._deny(
                action=action,
                reason="local_audit_external_effects_disabled",
                provider=provider,
                endpoint_hosts=endpoint_hosts,
                estimated_cost_usd=estimated_cost_usd,
            )
        return policy

    def _deny(
        self,
        *,
        action: str,
        reason: str,
        provider: str | None = None,
        endpoint_hosts: tuple[str, ...] = (),
        account_hash: str | None = None,
        estimated_cost_usd: float | None = None,
    ) -> None:
        self._record(
            LocalAuditDecision(
                action=action,
                allowed=False,
                reason=reason,
                provider=provider,
                endpoint_hosts=endpoint_hosts,
                account_hash=account_hash,
                estimated_cost_usd=estimated_cost_usd,
            )
        )
        raise LocalAuditEffectsDenied(reason)

    def _record(self, decision: LocalAuditDecision) -> None:
        with self._decision_lock:
            self._decisions.append(decision)


def local_audit_live_effects_gate_from_settings(
    settings: object,
) -> LocalAuditLiveEffectsGate | None:
    """Build the local-only gate; ordinary development and production stay unchanged."""

    return LocalAuditLiveEffectsGate.from_settings(settings)


def https_host_from_url(value: str | None) -> str | None:
    """Return a hostname only for credential-free HTTPS URLs."""

    if not value:
        return None
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or parsed.username or parsed.password:
        return None
    return _normalize_host(parsed.hostname)


def _parse_accounts(
    raw_value: object,
    *,
    field: str,
    errors: list[str],
) -> tuple[AuditAccount, ...]:
    payload: object = raw_value
    if isinstance(raw_value, str):
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError:
            errors.append(f"{field}_invalid")
            return ()
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        errors.append(f"{field}_required")
        return ()
    accounts: list[AuditAccount] = []
    for item in payload:
        if not isinstance(item, Mapping):
            errors.append(f"{field}_invalid")
            continue
        provider = _normalize_provider(item.get("provider"))
        account_id = str(item.get("account_id") or "").strip()
        if not provider or not _ACCOUNT_IDENTIFIER_PATTERN.fullmatch(account_id):
            errors.append(f"{field}_invalid")
            continue
        if not _NON_PRODUCTION_LABEL_PATTERN.search(account_id):
            errors.append(f"{field}_must_use_non_production_accounts")
            continue
        if _PRODUCTION_LABEL_PATTERN.search(account_id):
            errors.append(f"{field}_must_use_non_production_accounts")
            continue
        accounts.append(AuditAccount(provider=provider, account_id=account_id))
    if not accounts:
        errors.append(f"{field}_required")
    if len({(item.provider, item.account_id) for item in accounts}) != len(accounts):
        errors.append(f"{field}_duplicate")
    return tuple(accounts)


def _parse_domains(raw_value: object, errors: list[str]) -> tuple[str, ...]:
    if isinstance(raw_value, str):
        values = [item.strip() for item in raw_value.split(",") if item.strip()]
    elif isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)):
        values = [str(item).strip() for item in raw_value if str(item).strip()]
    else:
        values = []
    domains: list[str] = []
    for value in values:
        host = _normalize_host(value)
        if host is None:
            errors.append("approved_domains_invalid")
            continue
        if _is_production_motionly_host(host):
            errors.append("production_domain_not_allowed")
            continue
        domains.append(host)
    if not domains:
        errors.append("approved_domains_required")
    if len(set(domains)) != len(domains):
        errors.append("approved_domains_duplicate")
    return tuple(domains)


def _parse_visibilities(raw_value: object, errors: list[str]) -> tuple[str, ...]:
    if isinstance(raw_value, str):
        values = [item.strip().lower() for item in raw_value.split(",") if item.strip()]
    elif isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)):
        values = [str(item).strip().lower() for item in raw_value if str(item).strip()]
    else:
        values = []
    if not values:
        errors.append("publish_visibility_required")
        return ()
    if any(value not in _ALLOWED_PUBLISH_VISIBILITIES for value in values):
        errors.append("publish_visibility_invalid")
    if len(set(values)) != len(values):
        errors.append("publish_visibility_duplicate")
    return tuple(values)


def _parse_positive_amount(
    raw_value: object,
    missing_error: str,
    errors: list[str],
) -> float | None:
    try:
        amount = float(raw_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        errors.append(missing_error)
        return None
    if not math.isfinite(amount) or amount <= 0:
        errors.append(missing_error)
        return None
    return amount


def _required_string(raw_value: object, error: str, errors: list[str]) -> str | None:
    value = str(raw_value or "").strip()
    if _is_placeholder(value):
        errors.append(error)
        return None
    return value


def _is_placeholder(value: str | None, *, min_length: int = 1) -> bool:
    candidate = str(value or "").strip()
    if len(candidate) < min_length:
        return True
    lowered = candidate.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_provider(value: object) -> str:
    return str(value or "").strip().lower()


def _normalize_host(value: str | None) -> str | None:
    if not value:
        return None
    candidate = str(value).strip().lower().rstrip(".")
    if not candidate or "://" in candidate or "/" in candidate or "@" in candidate:
        return None
    if not _HOST_PATTERN.fullmatch(candidate):
        return None
    return candidate


def _is_production_motionly_host(host: str) -> bool:
    return any(host == root or host.endswith(f".{root}") for root in _PRODUCTION_MOTIONLY_HOSTS)


def _find_account(accounts: Sequence[AuditAccount], provider: str) -> AuditAccount | None:
    return next((account for account in accounts if account.provider == provider), None)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _round_currency(value: float) -> float:
    return round(value, 8)
