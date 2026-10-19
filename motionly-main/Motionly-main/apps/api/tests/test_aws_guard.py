"""cfn-lint and cfn-guard on the rendered template, and proof that they bite.

Each case breaks the template the way a careless change would and asserts
that the named Guard rule (or cfn-lint) rejects it, so a rule that silently
stops matching fails here. Needs the pinned tools from
scripts/aws/install-tools.sh; the 'aws' verify stage enables it.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = json.loads((ROOT / "infra" / "aws" / "motionly.template.json").read_text("utf-8"))
RULES = ROOT / "infra" / "aws" / "guard" / "motionly.guard"
TOOLS = Path(os.environ.get("MOTIONLY_AWS_TOOLS", "/tmp/motionly-aws-tools"))

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_AWS_STATIC_TOOLS") != "1",
    reason="AWS static tools are not installed here; set RUN_AWS_STATIC_TOOLS=1 after "
    "scripts/aws/install-tools.sh.",
)


def _guard(template: dict, tmp_path: Path) -> dict[str, str]:
    data = tmp_path / "template.json"
    data.write_text(json.dumps(template), encoding="utf-8")
    result = subprocess.run(
        [
            str(TOOLS / "cfn-guard"),
            "validate",
            "--rules",
            str(RULES),
            "--data",
            str(data),
            "--show-summary",
            "all",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    statuses: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].startswith("motionly.guard/"):
            statuses[parts[0].split("/", 1)[1]] = parts[1]
    assert statuses, result.stdout + result.stderr
    return statuses


def _lint(template: dict, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    data = tmp_path / "template.json"
    data.write_text(json.dumps(template), encoding="utf-8")
    return subprocess.run(
        [str(TOOLS / "venv" / "bin" / "cfn-lint"), "--regions", "ap-south-1", "--", str(data)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_template_passes_cfn_lint_and_every_guard_rule(tmp_path: Path) -> None:
    lint = _lint(TEMPLATE, tmp_path)
    assert lint.returncode == 0, lint.stdout + lint.stderr
    statuses = _guard(TEMPLATE, tmp_path)
    assert len(statuses) == 9
    assert set(statuses.values()) == {"PASS"}, statuses


def _mutate(path: list, value=None, *, delete: bool = False):
    def apply(template: dict) -> dict:
        changed = copy.deepcopy(template)
        target = changed
        for key in path[:-1]:
            target = target[key]
        if delete:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        return changed

    return apply


SG = ["Resources", "OriginSecurityGroup", "Properties", "SecurityGroupIngress"]
LT = ["Resources", "LaunchTemplate", "Properties", "LaunchTemplateData"]
DIST = ["Resources", "Distribution", "Properties", "DistributionConfig"]
TRUST = ["Resources", "GitHubDeployRole", "Properties", "AssumeRolePolicyDocument", "Statement", 0]
MEDIA = ["Resources", "MediaBucket", "Properties"]


def _open_ssh(template: dict) -> dict:
    changed = copy.deepcopy(template)
    ingress = changed["Resources"]["OriginSecurityGroup"]["Properties"]["SecurityGroupIngress"]
    ingress.append({"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "CidrIp": "0.0.0.0/0"})
    return changed


def _wildcard_action(template: dict) -> dict:
    changed = copy.deepcopy(template)
    statement = changed["Resources"]["InstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"][
        "Statement"
    ][1]
    statement["Action"] = ["s3:*"]
    return changed


def _wildcard_subject(template: dict) -> dict:
    changed = copy.deepcopy(template)
    condition = changed["Resources"]["GitHubDeployRole"]["Properties"]["AssumeRolePolicyDocument"][
        "Statement"
    ][0]["Condition"]
    condition["StringLike"] = {"token.actions.githubusercontent.com:sub": "repo:*"}
    return changed


def _plain_http_viewer(template: dict) -> dict:
    changed = copy.deepcopy(template)
    changed["Resources"]["Distribution"]["Properties"]["DistributionConfig"]["CacheBehaviors"][0][
        "ViewerProtocolPolicy"
    ] = "allow-all"
    return changed


@pytest.mark.parametrize(
    ("rule", "break_template"),
    [
        (
            "s3_buckets_private_encrypted_versioned",
            _mutate([*MEDIA, "PublicAccessBlockConfiguration", "BlockPublicPolicy"], False),
        ),
        (
            "s3_buckets_private_encrypted_versioned",
            _mutate([*MEDIA, "VersioningConfiguration", "Status"], "Suspended"),
        ),
        (
            "s3_buckets_private_encrypted_versioned",
            _mutate([*MEDIA, "BucketName"], "motionly-media"),
        ),
        (
            "s3_bucket_policies_deny_plain_http",
            _mutate(
                [
                    "Resources",
                    "MediaBucketPolicy",
                    "Properties",
                    "PolicyDocument",
                    "Statement",
                    0,
                    "Effect",
                ],
                "Allow",
            ),
        ),
        ("origin_reachable_only_from_cloudfront", _open_ssh),
        (
            "origin_reachable_only_from_cloudfront",
            _mutate([*SG, 0, "SourcePrefixListId"], delete=True),
        ),
        (
            "instances_imdsv2_encrypted_no_key_pair",
            _mutate([*LT, "MetadataOptions", "HttpTokens"], "optional"),
        ),
        ("instances_imdsv2_encrypted_no_key_pair", _mutate([*LT, "KeyName"], "operator")),
        (
            "instances_imdsv2_encrypted_no_key_pair",
            _mutate([*LT, "BlockDeviceMappings", 0, "Ebs", "Encrypted"], False),
        ),
        (
            "volumes_encrypted",
            _mutate(["Resources", "DataVolume", "Properties", "Encrypted"], False),
        ),
        ("iam_no_wildcard_actions", _wildcard_action),
        ("github_trust_bound_to_one_subject", _wildcard_subject),
        (
            "github_trust_bound_to_one_subject",
            _mutate(
                [*TRUST, "Condition", "StringEquals", "token.actions.githubusercontent.com:aud"],
                "anything",
            ),
        ),
        ("cloudfront_https_and_origin_secret", _plain_http_viewer),
        (
            "cloudfront_https_and_origin_secret",
            _mutate([*DIST, "Origins", 0, "OriginCustomHeaders"], []),
        ),
        (
            "cloudfront_https_and_origin_secret",
            _mutate([*DIST, "CustomErrorResponses", 0, "ErrorCachingMinTTL"], 10),
        ),
    ],
)
def test_each_guard_rule_rejects_the_change_it_exists_to_stop(
    rule: str, break_template, tmp_path: Path
) -> None:
    statuses = _guard(break_template(TEMPLATE), tmp_path)
    assert statuses[rule] == "FAIL", statuses


def test_cfn_lint_rejects_an_invalid_property(tmp_path: Path) -> None:
    broken = _mutate([*DIST, "CustomOriginTimeout"], 60)(TEMPLATE)
    assert _lint(broken, tmp_path).returncode != 0
    broken = _mutate([*DIST, "HttpVersion"], "http4")(TEMPLATE)
    assert _lint(broken, tmp_path).returncode != 0
