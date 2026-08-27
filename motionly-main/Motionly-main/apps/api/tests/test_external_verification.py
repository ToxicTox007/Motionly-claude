import pytest

from app.core.external_verification import live_provider_contracts_enabled


def test_live_provider_contracts_are_disabled_by_default() -> None:
    assert live_provider_contracts_enabled({}) is False
    assert live_provider_contracts_enabled({"LIVE_PROVIDER_CONTRACTS": "false"}) is False


@pytest.mark.parametrize(
    "environment, message",
    [
        (
            {
                "LIVE_PROVIDER_CONTRACTS": "true",
                "EXTERNAL_VERIFICATION_TARGET": "staging-sandbox",
                "EXTERNAL_VERIFICATION_RUN_ID": "run-1",
            },
            "protected authorization",
        ),
        (
            {
                "LIVE_PROVIDER_CONTRACTS": "true",
                "EXTERNAL_VERIFICATION_AUTHORIZATION": "a" * 16,
                "EXTERNAL_VERIFICATION_TARGET": "production",
                "EXTERNAL_VERIFICATION_RUN_ID": "run-1",
            },
            "staging-sandbox",
        ),
        (
            {
                "LIVE_PROVIDER_CONTRACTS": "true",
                "EXTERNAL_VERIFICATION_AUTHORIZATION": "a" * 16,
                "EXTERNAL_VERIFICATION_TARGET": "staging-sandbox",
            },
            "run identifier",
        ),
    ],
)
def test_live_provider_contracts_fail_closed_without_complete_authorization(
    environment: dict[str, str], message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        live_provider_contracts_enabled(environment)


def test_live_provider_contracts_require_a_protected_sandbox_run() -> None:
    assert live_provider_contracts_enabled(
        {
            "LIVE_PROVIDER_CONTRACTS": "true",
            "EXTERNAL_VERIFICATION_AUTHORIZATION": "protected-approval-token",
            "EXTERNAL_VERIFICATION_TARGET": "staging-sandbox",
            "EXTERNAL_VERIFICATION_RUN_ID": "github-run-123",
        }
    ) is True
