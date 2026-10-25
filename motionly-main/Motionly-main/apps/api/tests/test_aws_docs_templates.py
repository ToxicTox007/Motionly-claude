"""Contract tests for the hand-authored AWS docs templates in ``docs/aws``.

These templates (IAM policy, S3 bucket policies, S3 lifecycle rules, and the Bedrock Data
Automation standard-output config) ship with placeholders only. The tests assert they are
syntactically valid, scoped (no ``*`` resources, only the allowed Bedrock/S3 actions), free
of real identifiers (no 12-digit account ids except the dummy ``000000000000``, no
``AKIA``/``ASIA`` key ids, no emails except ``<ALERT_EMAIL>``), and that the regions and
lifecycle windows match the plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
AWS_DOCS_DIR = ROOT / "docs" / "aws"

IAM_POLICY_PATH = AWS_DOCS_DIR / "iam-inference-policy.json"
STAGING_BUCKET_POLICY_PATH = AWS_DOCS_DIR / "s3-staging-bucket-policy.json"
VIDEO_BUCKET_POLICY_PATH = AWS_DOCS_DIR / "s3-video-output-bucket-policy.json"
STAGING_LIFECYCLE_PATH = AWS_DOCS_DIR / "s3-staging-lifecycle.json"
VIDEO_LIFECYCLE_PATH = AWS_DOCS_DIR / "s3-video-output-lifecycle.json"
BDA_OUTPUT_PATH = AWS_DOCS_DIR / "bda-audio-standard-output.json"

ALL_JSON_PATHS = (
    IAM_POLICY_PATH,
    STAGING_BUCKET_POLICY_PATH,
    VIDEO_BUCKET_POLICY_PATH,
    STAGING_LIFECYCLE_PATH,
    VIDEO_LIFECYCLE_PATH,
    BDA_OUTPUT_PATH,
)

ALL_DOC_PATHS = (*ALL_JSON_PATHS, *sorted(AWS_DOCS_DIR.glob("*.md")))

# The only AWS inference actions the scoped policy is allowed to grant.
ALLOWED_IAM_ACTIONS = frozenset(
    {
        "bedrock:InvokeModel",
        "bedrock:GetAsyncInvoke",
        "bedrock:InvokeDataAutomationAsync",
        "bedrock:GetDataAutomationStatus",
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:ListBucket",
    }
)

# F3 destination regions for the Nova profiles (source ap-south-1).
NOVA_DESTINATION_REGIONS = frozenset(
    {
        "ap-northeast-1",
        "ap-northeast-2",
        "ap-northeast-3",
        "ap-south-1",
        "ap-southeast-1",
        "ap-southeast-2",
    }
)

# F5 destination regions for the APAC data-automation profile.
BDA_DESTINATION_REGIONS = frozenset(
    {
        "ap-northeast-1",
        "ap-northeast-2",
        "ap-northeast-3",
        "ap-south-1",
        "ap-south-2",
        "ap-southeast-1",
        "ap-southeast-2",
        "ap-southeast-4",
    }
)

PLACEHOLDER_TOKEN = re.compile(r"<[A-Z][A-Z0-9_]*>")
TWELVE_DIGIT_RUN = re.compile(r"(?<!\d)\d{12}(?!\d)")
ACCESS_KEY_ID = re.compile(r"(AKIA|ASIA)[A-Z0-9]{16}")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
FOUNDATION_MODEL_EMPTY_ACCOUNT = re.compile(
    r"^arn:aws:bedrock:[a-z0-9-]+::foundation-model/"
)


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_strings(node: object):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_strings(item)


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    return [value]


# ---------------------------------------------------------------------------
# Every template parses.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("json_path", ALL_JSON_PATHS, ids=lambda p: p.name)
def test_every_aws_docs_json_template_parses(json_path: Path) -> None:
    assert json_path.exists(), f"missing template: {json_path}"
    _load_json(json_path)  # raises on invalid JSON


# ---------------------------------------------------------------------------
# No real identifiers anywhere in the templates or their markdown.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc_path", ALL_DOC_PATHS, ids=lambda p: p.name)
def test_no_aws_access_key_ids_in_templates(doc_path: Path) -> None:
    text = doc_path.read_text(encoding="utf-8")
    assert not ACCESS_KEY_ID.search(text), f"AKIA/ASIA-style key id in {doc_path.name}"


@pytest.mark.parametrize("json_path", ALL_JSON_PATHS, ids=lambda p: p.name)
def test_no_twelve_digit_numbers_except_dummy_account(json_path: Path) -> None:
    for value in _iter_strings(_load_json(json_path)):
        for match in TWELVE_DIGIT_RUN.findall(value):
            assert match == "000000000000", (
                f"unexpected 12-digit run {match!r} in {json_path.name}; "
                "use the <ACCOUNT_ID> placeholder"
            )


@pytest.mark.parametrize("doc_path", ALL_DOC_PATHS, ids=lambda p: p.name)
def test_no_email_except_alert_placeholder(doc_path: Path) -> None:
    text = doc_path.read_text(encoding="utf-8")
    # ``<ALERT_EMAIL>`` is the only permitted email token; it is not matched by EMAIL.
    emails = EMAIL.findall(text)
    assert not emails, f"real-looking email(s) {emails} in {doc_path.name}"


# ---------------------------------------------------------------------------
# IAM policy structure.
# ---------------------------------------------------------------------------


def test_iam_policy_version_and_sids() -> None:
    policy = _load_json(IAM_POLICY_PATH)
    assert policy["Version"] == "2012-10-17"
    statements = policy["Statement"]
    assert statements, "IAM policy has no statements"
    sids = [stmt["Sid"] for stmt in statements]
    assert all(sids), "every statement needs a descriptive Sid"
    assert len(set(sids)) == len(sids), "Sids must be unique"


def test_iam_policy_only_allows_expected_actions() -> None:
    policy = _load_json(IAM_POLICY_PATH)
    for stmt in policy["Statement"]:
        assert stmt["Effect"] == "Allow"
        for action in _as_list(stmt["Action"]):
            assert action in ALLOWED_IAM_ACTIONS, f"unexpected action {action}"


def test_iam_policy_has_no_wildcard_resource() -> None:
    policy = _load_json(IAM_POLICY_PATH)
    for stmt in policy["Statement"]:
        for resource in _as_list(stmt["Resource"]):
            assert resource != "*", f"statement {stmt['Sid']} uses a '*' resource"
            # No bare service wildcard like ``arn:aws:s3:::*`` either.
            assert not resource.endswith(":::*"), (
                f"statement {stmt['Sid']} uses a bare bucket wildcard"
            )


def test_iam_policy_arns_use_placeholder_or_empty_account_foundation_model() -> None:
    policy = _load_json(IAM_POLICY_PATH)
    for stmt in policy["Statement"]:
        for resource in _as_list(stmt["Resource"]):
            has_placeholder = bool(PLACEHOLDER_TOKEN.search(resource))
            is_foundation_model = bool(FOUNDATION_MODEL_EMPTY_ACCOUNT.match(resource))
            assert has_placeholder or is_foundation_model, (
                f"resource {resource!r} in {stmt['Sid']} must contain a placeholder "
                "or be an empty-account foundation-model ARN"
            )
            if is_foundation_model:
                # Empty-account foundation-model ARNs must not carry an account id.
                account_field = resource.split(":")[4]
                assert account_field == "", (
                    f"foundation-model ARN {resource!r} must have an empty account field"
                )


def test_iam_policy_nova_regions_match_facts() -> None:
    policy = _load_json(IAM_POLICY_PATH)
    foundation_regions_pro: set[str] = set()
    foundation_regions_lite: set[str] = set()
    for stmt in policy["Statement"]:
        if stmt["Sid"] != "InvokeNovaFoundationModelsInDestinationRegions":
            continue
        for resource in _as_list(stmt["Resource"]):
            region = resource.split(":")[3]
            if resource.endswith("amazon.nova-pro-v1:0"):
                foundation_regions_pro.add(region)
            elif resource.endswith("amazon.nova-lite-v1:0"):
                foundation_regions_lite.add(region)
    assert foundation_regions_pro == NOVA_DESTINATION_REGIONS
    assert foundation_regions_lite == NOVA_DESTINATION_REGIONS


def test_iam_policy_bda_profile_regions_match_facts() -> None:
    policy = _load_json(IAM_POLICY_PATH)
    profile_regions: set[str] = set()
    has_project_arn = False
    for stmt in policy["Statement"]:
        if stmt["Sid"] != "InvokeDataAutomationApacProfileAndProject":
            continue
        for resource in _as_list(stmt["Resource"]):
            if "data-automation-profile/apac.data-automation-v1" in resource:
                profile_regions.add(resource.split(":")[3])
            elif "data-automation-project/" in resource:
                has_project_arn = True
    assert profile_regions == BDA_DESTINATION_REGIONS
    assert has_project_arn, "BDA statement must also grant the project ARN"


def test_iam_policy_titan_and_video_regions() -> None:
    policy = _load_json(IAM_POLICY_PATH)
    by_sid = {stmt["Sid"]: stmt for stmt in policy["Statement"]}
    titan = by_sid["InvokeTitanEmbeddingsApSouth1"]
    assert "ap-south-1" in _as_list(titan["Resource"])[0]
    assert "amazon.titan-embed-text-v2:0" in _as_list(titan["Resource"])[0]
    video = by_sid["InvokeLumaRayVideoUsWest2"]
    assert "us-west-2" in _as_list(video["Resource"])[0]
    assert "luma.ray-v2:0" in _as_list(video["Resource"])[0]
    async_status = by_sid["ReadAsyncInvokeStatusUsWest2"]
    assert async_status["Action"] == "bedrock:GetAsyncInvoke"
    assert "us-west-2" in _as_list(async_status["Resource"])[0]


def test_iam_policy_has_no_start_async_invoke_action() -> None:
    text = IAM_POLICY_PATH.read_text(encoding="utf-8")
    assert "StartAsyncInvoke" not in text, (
        "there is no bedrock:StartAsyncInvoke IAM action (F1)"
    )


# ---------------------------------------------------------------------------
# Bucket policies: deny non-TLS, reference placeholders, lock to two principals.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "policy_path",
    (STAGING_BUCKET_POLICY_PATH, VIDEO_BUCKET_POLICY_PATH),
    ids=lambda p: p.name,
)
def test_bucket_policy_denies_non_tls_and_scopes_principals(policy_path: Path) -> None:
    policy = _load_json(policy_path)
    assert policy["Version"] == "2012-10-17"
    statements = policy["Statement"]
    assert all(stmt["Effect"] == "Deny" for stmt in statements)

    secure_transport = [
        stmt
        for stmt in statements
        if stmt.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport") == "false"
    ]
    assert secure_transport, f"{policy_path.name} must deny non-TLS requests"

    principal_scoped = [
        stmt
        for stmt in statements
        if "StringNotLike" in stmt.get("Condition", {})
        and "aws:PrincipalArn" in stmt["Condition"]["StringNotLike"]
    ]
    assert principal_scoped, f"{policy_path.name} must scope by aws:PrincipalArn"
    allowed_principals = principal_scoped[0]["Condition"]["StringNotLike"]["aws:PrincipalArn"]
    joined = " ".join(allowed_principals)
    assert "user/motionly-inference" in joined
    assert "<BREAK_GLASS_ROLE_NAME>" in joined

    # Every resource references the bucket placeholder.
    for stmt in statements:
        for resource in _as_list(stmt["Resource"]):
            assert PLACEHOLDER_TOKEN.search(resource), (
                f"resource {resource!r} in {policy_path.name} must use a placeholder"
            )


def test_staging_bucket_policy_references_staging_placeholder() -> None:
    text = STAGING_BUCKET_POLICY_PATH.read_text(encoding="utf-8")
    assert "<STAGING_BUCKET>" in text
    assert "<VIDEO_OUTPUT_BUCKET>" not in text


def test_video_bucket_policy_references_video_placeholder() -> None:
    text = VIDEO_BUCKET_POLICY_PATH.read_text(encoding="utf-8")
    assert "<VIDEO_OUTPUT_BUCKET>" in text
    assert "<STAGING_BUCKET>" not in text


# ---------------------------------------------------------------------------
# Lifecycle rules: staging expires after 1 day, video after 7 days.
# ---------------------------------------------------------------------------


def test_staging_lifecycle_expires_bda_prefix_after_one_day() -> None:
    config = _load_json(STAGING_LIFECYCLE_PATH)
    rule = config["Rules"][0]
    assert rule["Status"] == "Enabled"
    assert rule["Filter"]["Prefix"] == "bda/"
    assert rule["Expiration"]["Days"] == 1
    assert rule["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 1


def test_video_lifecycle_expires_video_prefix_after_seven_days() -> None:
    config = _load_json(VIDEO_LIFECYCLE_PATH)
    rule = config["Rules"][0]
    assert rule["Status"] == "Enabled"
    assert rule["Filter"]["Prefix"] == "video/"
    assert rule["Expiration"]["Days"] == 7
    assert rule["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 1


# ---------------------------------------------------------------------------
# BDA standard-output config enables the transcript category with speaker labels.
# ---------------------------------------------------------------------------


def test_bda_output_enables_transcript_with_speaker_labeling() -> None:
    config = _load_json(BDA_OUTPUT_PATH)
    category = config["audio"]["extraction"]["category"]
    assert category["state"] == "ENABLED"
    assert category["types"] == ["TRANSCRIPT"]
    speaker = category["typeConfiguration"]["transcript"]["speakerLabeling"]
    assert speaker["state"] == "ENABLED"
