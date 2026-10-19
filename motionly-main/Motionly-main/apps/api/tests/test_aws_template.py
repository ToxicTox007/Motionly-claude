"""The AWS CloudFormation template (Task 8, static validation only).

infra/aws/motionly.template.json is rendered from infra/aws/edge-contract.json
by scripts/aws/render_template.py. These tests pin that the committed template
is the rendered one, that CloudFront matches the edge contract the CPU-stage
simulator applies, and the security decisions: only CloudFront reaches the
origin (prefix list plus origin secret), no SSH, IMDSv2, encrypted storage,
private TLS-only buckets, and a GitHub deploy role bound to one repository and
environment. cfn-lint and cfn-guard run in `pnpm verify --stage aws`.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = ROOT / "infra" / "aws" / "motionly.template.json"
CONTRACT = json.loads((ROOT / "infra" / "aws" / "edge-contract.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def renderer():
    spec = importlib.util.spec_from_file_location(
        "motionly_render_template", ROOT / "scripts" / "aws" / "render_template.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text("utf-8"))


def _of_type(template: dict, resource_type: str) -> dict:
    return {
        name: resource
        for name, resource in template["Resources"].items()
        if resource["Type"] == resource_type
    }


def _one(template: dict, resource_type: str) -> dict:
    resources = _of_type(template, resource_type)
    assert len(resources) == 1, (resource_type, sorted(resources))
    return next(iter(resources.values()))


def test_the_committed_template_is_the_rendered_one(renderer, template) -> None:
    assert renderer.render(CONTRACT) == template, (
        "run python scripts/aws/render_template.py to update infra/aws/motionly.template.json"
    )


def test_the_stack_deploys_only_to_mumbai_with_an_explicit_compute_profile(template) -> None:
    assert template["Parameters"]["ComputeProfile"]["AllowedValues"] == ["cpu", "gpu"]
    assert "Default" not in template["Parameters"]["ComputeProfile"]
    profiles = template["Mappings"]["ComputeProfiles"]
    assert profiles["gpu"]["InstanceType"] == "g6.12xlarge"
    assert profiles["cpu"]["InstanceType"] != "g6.12xlarge"
    assertion = template["Rules"]["MumbaiOnly"]["Assertions"][0]["Assert"]
    assert assertion == {"Fn::Equals": [{"Ref": "AWS::Region"}, "ap-south-1"]}
    gpu_image = template["Parameters"]["GpuImageId"]
    assert gpu_image["Type"] == "AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>"
    assert "base-oss-nvidia-driver-gpu-amazon-linux-2023" in gpu_image["Default"]


def test_cloudfront_matches_the_edge_contract(template) -> None:
    config = _one(template, "AWS::CloudFront::Distribution")["Properties"]["DistributionConfig"]
    distribution = CONTRACT["distribution"]
    assert config["PriceClass"] == distribution["price_class"]
    assert config["HttpVersion"] == distribution["http_versions"]
    assert config["IPV6Enabled"] is distribution["ipv6"]
    assert config["ViewerCertificate"] == {"CloudFrontDefaultCertificate": True}

    (origin,) = config["Origins"]
    contract_origin = CONTRACT["origin"]
    custom = origin["CustomOriginConfig"]
    assert origin["Id"] == contract_origin["id"]
    assert origin["ConnectionAttempts"] == contract_origin["connection_attempts"]
    assert origin["ConnectionTimeout"] == contract_origin["connection_timeout_seconds"]
    assert custom == {
        "HTTPPort": contract_origin["http_port"],
        "OriginProtocolPolicy": contract_origin["protocol_policy"],
        "OriginReadTimeout": contract_origin["response_timeout_seconds"],
        "OriginKeepaliveTimeout": contract_origin["keepalive_timeout_seconds"],
    }
    assert "ResponseCompletionTimeout" not in origin
    assert origin["OriginCustomHeaders"] == [
        {"HeaderName": "X-Motionly-Origin-Verify", "HeaderValue": {"Ref": "OriginVerifySecret"}}
    ]

    behaviors = [*config["CacheBehaviors"], config["DefaultCacheBehavior"]]
    assert [b.get("PathPattern", "*") for b in behaviors] == [
        b["path_pattern"] for b in CONTRACT["behaviors"]
    ]
    for rendered, expected in zip(behaviors, CONTRACT["behaviors"], strict=True):
        assert rendered["TargetOriginId"] == contract_origin["id"]
        assert rendered["ViewerProtocolPolicy"] == expected["viewer_protocol_policy"]
        assert rendered["AllowedMethods"] == expected["allowed_methods"]
        assert rendered["CachedMethods"] == expected["cached_methods"]
        assert rendered["Compress"] is expected["compress"]
        assert (
            rendered["CachePolicyId"]
            == (CONTRACT["cache_policies"][expected["cache_policy"]]["managed_id"])
        )
        assert (
            rendered["OriginRequestPolicyId"]
            == (
                CONTRACT["origin_request_policies"][expected["origin_request_policy"]]["managed_id"]
            )
        )
        assert rendered["ResponseHeadersPolicyId"] == {"Ref": "EdgeSecurityHeaders"}

    errors = config["CustomErrorResponses"]
    assert [e["ErrorCode"] for e in errors] == CONTRACT["error_caching"]["status_codes"]
    assert all(e == {"ErrorCode": e["ErrorCode"], "ErrorCachingMinTTL": 0} for e in errors)


def test_the_response_headers_policy_matches_the_edge_contract(template) -> None:
    policy = template["Resources"]["EdgeSecurityHeaders"]["Properties"][
        "ResponseHeadersPolicyConfig"
    ]
    contract = CONTRACT["response_headers_policies"]["motionly-edge-security"]
    security = policy["SecurityHeadersConfig"]
    hsts = contract["strict_transport_security"]
    assert security["StrictTransportSecurity"] == {
        "AccessControlMaxAgeSec": hsts["max_age_seconds"],
        "IncludeSubdomains": hsts["include_subdomains"],
        "Preload": hsts["preload"],
        "Override": hsts["override"],
    }
    assert security["ContentTypeOptions"] == {"Override": False}
    assert security["FrameOptions"] == {"FrameOption": "DENY", "Override": False}
    assert security["ReferrerPolicy"] == {
        "ReferrerPolicy": "strict-origin-when-cross-origin",
        "Override": False,
    }
    assert "ContentSecurityPolicy" not in security
    assert policy["RemoveHeadersConfig"]["Items"] == [
        {"Header": name} for name in contract["remove_headers"]
    ]


def test_only_cloudfront_can_reach_the_origin_and_nothing_listens_for_ssh(template) -> None:
    (group,) = _of_type(template, "AWS::EC2::SecurityGroup").values()
    assert group["Properties"]["SecurityGroupIngress"] == [
        {
            "IpProtocol": "tcp",
            "FromPort": 80,
            "ToPort": 80,
            "SourcePrefixListId": {"Ref": "CloudFrontOriginPrefixListId"},
            "Description": "CloudFront origin-facing servers only",
        }
    ]
    assert not _of_type(template, "AWS::EC2::SecurityGroupIngress")
    for rule in group["Properties"]["SecurityGroupEgress"]:
        assert rule["IpProtocol"] == "tcp" and rule["FromPort"] == rule["ToPort"] == 443
    secret = template["Parameters"]["OriginVerifySecret"]
    assert secret["NoEcho"] is True
    assert secret["AllowedPattern"] == CONTRACT["origin_guard"]["value_format"]
    assert re.fullmatch(
        r"\^pl-.*\$", template["Parameters"]["CloudFrontOriginPrefixListId"]["AllowedPattern"]
    )


def test_the_origin_domain_is_the_elastic_ip_public_dns_name(template) -> None:
    config = _one(template, "AWS::CloudFront::Distribution")["Properties"]["DistributionConfig"]
    domain = json.dumps(config["Origins"][0]["DomainName"])
    assert '"Ref": "OriginAddress"' in domain
    assert ".ap-south-1.compute.amazonaws.com" in domain
    vpc = _one(template, "AWS::EC2::VPC")["Properties"]
    assert vpc["EnableDnsHostnames"] is True and vpc["EnableDnsSupport"] is True


def test_the_instance_uses_imdsv2_encrypted_disks_and_no_key_pair(template) -> None:
    data = _one(template, "AWS::EC2::LaunchTemplate")["Properties"]["LaunchTemplateData"]
    assert data["MetadataOptions"] == {
        "HttpEndpoint": "enabled",
        "HttpTokens": "required",
        "HttpPutResponseHopLimit": 1,
    }
    assert "KeyName" not in data
    assert all(device["Ebs"]["Encrypted"] is True for device in data["BlockDeviceMappings"])
    assert data["NetworkInterfaces"][0]["AssociatePublicIpAddress"] is False
    volume = _one(template, "AWS::EC2::Volume")
    assert volume["Properties"]["Encrypted"] is True
    assert volume["DeletionPolicy"] == volume["UpdateReplacePolicy"] == "Snapshot"
    assert "KeyName" not in _one(template, "AWS::EC2::Instance")["Properties"]


def test_buckets_are_private_encrypted_versioned_and_tls_only(template) -> None:
    buckets = _of_type(template, "AWS::S3::Bucket")
    assert sorted(buckets) == ["BackupsBucket", "MediaBucket", "ReleasesBucket"]
    policies = {
        policy["Properties"]["Bucket"]["Ref"]: policy["Properties"]["PolicyDocument"]
        for policy in _of_type(template, "AWS::S3::BucketPolicy").values()
    }
    for name, bucket in buckets.items():
        properties = bucket["Properties"]
        assert "BucketName" not in properties, "generated names cannot collide"
        assert properties["PublicAccessBlockConfiguration"] == {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
        assert properties["OwnershipControls"] == {
            "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
        }
        assert properties["VersioningConfiguration"] == {"Status": "Enabled"}
        sse = properties["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
        assert sse["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
        statements = policies[name]["Statement"]
        assert any(
            s["Effect"] == "Deny" and s["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}
            for s in statements
        ), name
    for name in ("MediaBucket", "BackupsBucket"):
        assert buckets[name]["DeletionPolicy"] == buckets[name]["UpdateReplacePolicy"] == "Retain"


def _statements(role: dict) -> list[dict]:
    return [
        statement
        for policy in role["Properties"].get("Policies", [])
        for statement in policy["PolicyDocument"]["Statement"]
    ]


def test_iam_grants_no_wildcard_actions_and_scopes_secrets_to_the_environment(template) -> None:
    for name, role in _of_type(template, "AWS::IAM::Role").items():
        for statement in _statements(role):
            actions = statement["Action"]
            assert isinstance(actions, list), name
            assert all("*" not in action for action in actions), (name, actions)
    instance = template["Resources"]["InstanceRole"]
    parameters = next(s for s in _statements(instance) if "ssm:GetParametersByPath" in s["Action"])
    assert json.dumps(parameters["Resource"]).count("parameter/motionly/${Environment}/*") == 1
    assert (
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
        in (instance["Properties"]["ManagedPolicyArns"])
    )
    backups = next(s for s in _statements(instance) if "BackupsBucket" in json.dumps(s["Resource"]))
    assert "s3:DeleteObject" not in backups["Action"], "the instance cannot delete backups"


def test_github_can_deploy_only_from_one_repository_environment(template) -> None:
    role = template["Resources"]["GitHubDeployRole"]
    (trust,) = role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    assert trust["Action"] == ["sts:AssumeRoleWithWebIdentity"]
    condition = trust["Condition"]
    assert set(condition) == {"StringEquals"}
    assert (
        condition["StringEquals"]["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    )
    assert condition["StringEquals"]["token.actions.githubusercontent.com:sub"] == {
        "Fn::Sub": "repo:${GitHubRepository}:environment:${GitHubDeployEnvironment}"
    }
    send = next(s for s in _statements(role) if "ssm:SendCommand" in s["Action"])
    resources = json.dumps(send["Resource"])
    assert "document/AWS-RunShellScript" in resources and "instance/${Instance}" in resources
    provider = _one(template, "AWS::IAM::OIDCProvider")
    assert provider["Condition"] == "CreateGitHubOidcProvider"
    assert provider["Properties"]["Url"] == "https://token.actions.githubusercontent.com"


def test_the_bootstrap_script_mounts_data_and_installs_a_pinned_compose(template) -> None:
    script = (ROOT / "infra" / "aws" / "bootstrap.sh").read_text("utf-8")
    assert script.startswith("#!/bin/bash\n")
    assert "set -euo pipefail" in script
    assert re.search(r"COMPOSE_VERSION=v\d+\.\d+\.\d+", script)
    assert re.search(r"COMPOSE_SHA256=[0-9a-f]{64}", script)
    assert "sha256sum -c" in script
    assert "/srv/motionly" in script and "nofail" in script
    user_data = json.dumps(
        _one(template, "AWS::EC2::LaunchTemplate")["Properties"]["LaunchTemplateData"]["UserData"]
    )
    assert "MOTIONLY_DATA_VOLUME_ID=" in user_data
