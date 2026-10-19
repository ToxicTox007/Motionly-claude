#!/usr/bin/env python3
"""Render infra/aws/motionly.template.json from infra/aws/edge-contract.json.

The edge contract is the single source of truth for CloudFront: the same file
drives the CPU-stage CDN simulator (scripts/cdn-sim). Everything else in the
stack (VPC, origin instance, buckets, GitHub deploy role) is fixed here.

    python scripts/aws/render_template.py           # write the template
    python scripts/aws/render_template.py --check   # fail if it is out of date

Static validation only: nothing here talks to AWS (cfn-lint and cfn-guard run
in `pnpm verify --stage aws`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "infra" / "aws" / "edge-contract.json"
TEMPLATE_PATH = ROOT / "infra" / "aws" / "motionly.template.json"
BOOTSTRAP_PATH = ROOT / "infra" / "aws" / "bootstrap.sh"

REGION = "ap-south-1"
GPU_IMAGE = (
    "/aws/service/deeplearning/ami/x86_64/"
    "base-oss-nvidia-driver-gpu-amazon-linux-2023/latest/ami-id"
)
CPU_IMAGE = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
SSM_CORE = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
GITHUB_OIDC = "token.actions.githubusercontent.com"


def _sub(text: str) -> dict:
    return {"Fn::Sub": text}


def _tags(name: str) -> list[dict]:
    return [
        {"Key": "Name", "Value": _sub(f"motionly-${{Environment}}-{name}")},
        {"Key": "motionly:environment", "Value": {"Ref": "Environment"}},
    ]


def _parameters(contract: dict) -> dict:
    return {
        "Environment": {
            "Type": "String",
            "AllowedValues": ["production", "staging"],
            "Description": "Names resources and scopes secrets to /motionly/<environment>/.",
        },
        "ComputeProfile": {
            "Type": "String",
            "AllowedValues": ["cpu", "gpu"],
            "Description": "gpu: the production g6.12xlarge GPU instance. cpu: a CPU-only "
            "instance for rehearsals; GPU-dependent capabilities are unavailable on it.",
        },
        "CpuImageId": {
            "Type": "AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>",
            "Default": CPU_IMAGE,
            "Description": "Amazon Linux 2023 image for the cpu profile.",
        },
        "GpuImageId": {
            "Type": "AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>",
            "Default": GPU_IMAGE,
            "Description": "Deep Learning Base OSS NVIDIA Driver GPU AMI (Amazon Linux 2023) "
            "for the gpu profile.",
        },
        "AvailabilityZone": {
            "Type": "AWS::EC2::AvailabilityZone::Name",
            "Default": "ap-south-1a",
            "Description": "Zone with g6.12xlarge capacity; the data volume lives here.",
        },
        "CloudFrontOriginPrefixListId": {
            "Type": "String",
            "AllowedPattern": "^pl-[0-9a-f]{8,17}$",
            "Description": "Id of the com.amazonaws.global.cloudfront.origin-facing managed "
            "prefix list in ap-south-1 (aws ec2 describe-managed-prefix-lists).",
        },
        "OriginVerifySecret": {
            "Type": "String",
            "NoEcho": True,
            "AllowedPattern": contract["origin_guard"]["value_format"],
            "Description": "Current value of SSM /motionly/<environment>/edge/origin-verify. "
            "CloudFront sends it to the origin; the origin rejects requests without it.",
        },
        "GitHubRepository": {
            "Type": "String",
            "AllowedPattern": "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
            "Description": "owner/name of the repository whose workflows may deploy.",
        },
        "GitHubDeployEnvironment": {
            "Type": "String",
            "Default": "production",
            "AllowedPattern": "^[A-Za-z0-9_.-]+$",
            "Description": "GitHub environment the deploy job runs in.",
        },
        "GitHubOidcProviderArn": {
            "Type": "String",
            "Default": "",
            "Description": "Existing GitHub OIDC provider ARN; leave empty to create one.",
        },
        "RootVolumeSizeGiB": {"Type": "Number", "Default": 100, "MinValue": 50, "MaxValue": 1000},
        "DataVolumeSizeGiB": {"Type": "Number", "Default": 500, "MinValue": 100, "MaxValue": 16000},
    }


def _network() -> dict:
    return {
        "Vpc": {
            "Type": "AWS::EC2::VPC",
            "Properties": {
                "CidrBlock": "10.40.0.0/16",
                "EnableDnsSupport": True,
                "EnableDnsHostnames": True,
                "Tags": _tags("vpc"),
            },
        },
        "InternetGateway": {
            "Type": "AWS::EC2::InternetGateway",
            "Properties": {"Tags": _tags("igw")},
        },
        "GatewayAttachment": {
            "Type": "AWS::EC2::VPCGatewayAttachment",
            "Properties": {
                "VpcId": {"Ref": "Vpc"},
                "InternetGatewayId": {"Ref": "InternetGateway"},
            },
        },
        "PublicSubnet": {
            "Type": "AWS::EC2::Subnet",
            "Properties": {
                "VpcId": {"Ref": "Vpc"},
                "CidrBlock": "10.40.0.0/24",
                "AvailabilityZone": {"Ref": "AvailabilityZone"},
                "MapPublicIpOnLaunch": False,
                "Tags": _tags("public"),
            },
        },
        "PublicRouteTable": {
            "Type": "AWS::EC2::RouteTable",
            "Properties": {"VpcId": {"Ref": "Vpc"}, "Tags": _tags("public")},
        },
        "DefaultRoute": {
            "Type": "AWS::EC2::Route",
            "DependsOn": "GatewayAttachment",
            "Properties": {
                "RouteTableId": {"Ref": "PublicRouteTable"},
                "DestinationCidrBlock": "0.0.0.0/0",
                "GatewayId": {"Ref": "InternetGateway"},
            },
        },
        "PublicSubnetRoutes": {
            "Type": "AWS::EC2::SubnetRouteTableAssociation",
            "Properties": {
                "SubnetId": {"Ref": "PublicSubnet"},
                "RouteTableId": {"Ref": "PublicRouteTable"},
            },
        },
        "OriginSecurityGroup": {
            "Type": "AWS::EC2::SecurityGroup",
            "Properties": {
                "GroupDescription": "Motionly origin: HTTP from CloudFront only, HTTPS out",
                "VpcId": {"Ref": "Vpc"},
                "SecurityGroupIngress": [
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 80,
                        "ToPort": 80,
                        "SourcePrefixListId": {"Ref": "CloudFrontOriginPrefixListId"},
                        "Description": "CloudFront origin-facing servers only",
                    }
                ],
                "SecurityGroupEgress": [
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 443,
                        "ToPort": 443,
                        "CidrIp": "0.0.0.0/0",
                        "Description": "HTTPS to AWS APIs, image registries and providers",
                    }
                ],
                "Tags": _tags("origin"),
            },
        },
    }


def _bucket(name: str, *, retain: bool, noncurrent_days: int) -> dict:
    resource = {
        "Type": "AWS::S3::Bucket",
        "Properties": {
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {
                        "ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                        "BucketKeyEnabled": True,
                    }
                ]
            },
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
            "VersioningConfiguration": {"Status": "Enabled"},
            "LifecycleConfiguration": {
                "Rules": [
                    {
                        "Id": "expire-old-versions",
                        "Status": "Enabled",
                        "NoncurrentVersionExpiration": {"NoncurrentDays": noncurrent_days},
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
                    }
                ]
            },
            "Tags": _tags(name),
        },
    }
    if retain:
        resource["DeletionPolicy"] = "Retain"
        resource["UpdateReplacePolicy"] = "Retain"
    return resource


def _tls_only(bucket: str) -> dict:
    return {
        "Type": "AWS::S3::BucketPolicy",
        "Properties": {
            "Bucket": {"Ref": bucket},
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "DenyInsecureTransport",
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": ["s3:*"],
                        "Resource": [
                            {"Fn::GetAtt": [bucket, "Arn"]},
                            _sub(f"${{{bucket}.Arn}}/*"),
                        ],
                        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                    }
                ],
            },
        },
    }


def _storage() -> dict:
    resources = {
        "ReleasesBucket": _bucket("releases", retain=False, noncurrent_days=90),
        "MediaBucket": _bucket("media", retain=True, noncurrent_days=30),
        "BackupsBucket": _bucket("backups", retain=True, noncurrent_days=35),
    }
    for name in list(resources):
        resources[f"{name}Policy"] = _tls_only(name)
    return resources


def _instance() -> dict:
    bootstrap = BOOTSTRAP_PATH.read_text(encoding="utf-8")
    user_data = {
        "Fn::Base64": {
            "Fn::Join": [
                "",
                [
                    "#!/bin/bash\nMOTIONLY_DATA_VOLUME_ID=",
                    {"Ref": "DataVolume"},
                    "\nexport MOTIONLY_DATA_VOLUME_ID\n",
                    bootstrap,
                ],
            ]
        }
    }
    bucket_arns = {
        name: [{"Fn::GetAtt": [name, "Arn"]}, _sub(f"${{{name}.Arn}}/*")]
        for name in ("ReleasesBucket", "MediaBucket", "BackupsBucket")
    }
    return {
        "InstanceRole": {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": ["sts:AssumeRole"],
                        }
                    ],
                },
                # SSM Run Command and Session Manager instead of SSH.
                "ManagedPolicyArns": [SSM_CORE],
                "Policies": [
                    {
                        "PolicyName": "motionly-runtime",
                        "PolicyDocument": {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Sid": "ReadEnvironmentSecrets",
                                    "Effect": "Allow",
                                    "Action": [
                                        "ssm:GetParameter",
                                        "ssm:GetParameters",
                                        "ssm:GetParametersByPath",
                                    ],
                                    "Resource": [
                                        _sub(
                                            "arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}"
                                            ":parameter/motionly/${Environment}/*"
                                        )
                                    ],
                                },
                                {
                                    "Sid": "ReadReleases",
                                    "Effect": "Allow",
                                    "Action": ["s3:GetObject", "s3:ListBucket"],
                                    "Resource": bucket_arns["ReleasesBucket"],
                                },
                                {
                                    "Sid": "ReadWriteMedia",
                                    "Effect": "Allow",
                                    "Action": [
                                        "s3:GetObject",
                                        "s3:PutObject",
                                        "s3:DeleteObject",
                                        "s3:ListBucket",
                                        "s3:AbortMultipartUpload",
                                        "s3:ListMultipartUploadParts",
                                    ],
                                    "Resource": bucket_arns["MediaBucket"],
                                },
                                {
                                    "Sid": "WriteBackups",
                                    "Effect": "Allow",
                                    "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                                    "Resource": bucket_arns["BackupsBucket"],
                                },
                            ],
                        },
                    }
                ],
                "Tags": _tags("instance"),
            },
        },
        "InstanceProfile": {
            "Type": "AWS::IAM::InstanceProfile",
            "Properties": {"Roles": [{"Ref": "InstanceRole"}]},
        },
        "DataVolume": {
            "Type": "AWS::EC2::Volume",
            "DeletionPolicy": "Snapshot",
            "UpdateReplacePolicy": "Snapshot",
            "Properties": {
                "AvailabilityZone": {"Ref": "AvailabilityZone"},
                "Size": {"Ref": "DataVolumeSizeGiB"},
                "VolumeType": "gp3",
                "Encrypted": True,
                "Tags": _tags("data"),
            },
        },
        "LaunchTemplate": {
            "Type": "AWS::EC2::LaunchTemplate",
            "Properties": {
                "LaunchTemplateData": {
                    "ImageId": {"Fn::If": ["UseGpu", {"Ref": "GpuImageId"}, {"Ref": "CpuImageId"}]},
                    "InstanceType": {
                        "Fn::FindInMap": [
                            "ComputeProfiles",
                            {"Ref": "ComputeProfile"},
                            "InstanceType",
                        ]
                    },
                    "IamInstanceProfile": {"Arn": {"Fn::GetAtt": ["InstanceProfile", "Arn"]}},
                    # IMDSv2 only; a hop limit of 1 keeps containers away from instance credentials.
                    "MetadataOptions": {
                        "HttpEndpoint": "enabled",
                        "HttpTokens": "required",
                        "HttpPutResponseHopLimit": 1,
                    },
                    "BlockDeviceMappings": [
                        {
                            "DeviceName": "/dev/xvda",
                            "Ebs": {
                                "VolumeSize": {"Ref": "RootVolumeSizeGiB"},
                                "VolumeType": "gp3",
                                "Encrypted": True,
                                "DeleteOnTermination": True,
                            },
                        }
                    ],
                    "NetworkInterfaces": [
                        {
                            "DeviceIndex": 0,
                            "SubnetId": {"Ref": "PublicSubnet"},
                            "Groups": [{"Ref": "OriginSecurityGroup"}],
                            "AssociatePublicIpAddress": False,
                        }
                    ],
                    "UserData": user_data,
                    "TagSpecifications": [{"ResourceType": "instance", "Tags": _tags("origin")}],
                }
            },
        },
        "Instance": {
            "Type": "AWS::EC2::Instance",
            "Properties": {
                "LaunchTemplate": {
                    "LaunchTemplateId": {"Ref": "LaunchTemplate"},
                    "Version": {"Fn::GetAtt": ["LaunchTemplate", "LatestVersionNumber"]},
                },
                "AvailabilityZone": {"Ref": "AvailabilityZone"},
            },
        },
        "DataVolumeAttachment": {
            "Type": "AWS::EC2::VolumeAttachment",
            "Properties": {
                "InstanceId": {"Ref": "Instance"},
                "VolumeId": {"Ref": "DataVolume"},
                "Device": "/dev/sdf",
            },
        },
        "OriginAddress": {
            "Type": "AWS::EC2::EIP",
            "DependsOn": "GatewayAttachment",
            "Properties": {
                "Domain": "vpc",
                "InstanceId": {"Ref": "Instance"},
                "Tags": _tags("origin"),
            },
        },
    }


def _deploy() -> dict:
    provider_arn = {
        "Fn::If": [
            "CreateGitHubOidcProvider",
            {"Ref": "GitHubOidcProvider"},
            {"Ref": "GitHubOidcProviderArn"},
        ]
    }
    return {
        "GitHubOidcProvider": {
            "Type": "AWS::IAM::OIDCProvider",
            "Condition": "CreateGitHubOidcProvider",
            "Properties": {"Url": f"https://{GITHUB_OIDC}", "ClientIdList": ["sts.amazonaws.com"]},
        },
        "GitHubDeployRole": {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "MaxSessionDuration": 3600,
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Federated": provider_arn},
                            "Action": ["sts:AssumeRoleWithWebIdentity"],
                            "Condition": {
                                "StringEquals": {
                                    f"{GITHUB_OIDC}:aud": "sts.amazonaws.com",
                                    f"{GITHUB_OIDC}:sub": _sub(
                                        "repo:${GitHubRepository}:environment:${GitHubDeployEnvironment}"
                                    ),
                                }
                            },
                        }
                    ],
                },
                "Policies": [
                    {
                        "PolicyName": "motionly-deploy",
                        "PolicyDocument": {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Sid": "UploadReleases",
                                    "Effect": "Allow",
                                    "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
                                    "Resource": [
                                        {"Fn::GetAtt": ["ReleasesBucket", "Arn"]},
                                        _sub("${ReleasesBucket.Arn}/*"),
                                    ],
                                },
                                {
                                    "Sid": "RunReleaseOnTheInstance",
                                    "Effect": "Allow",
                                    "Action": ["ssm:SendCommand"],
                                    "Resource": [
                                        _sub(
                                            "arn:${AWS::Partition}:ssm:${AWS::Region}::document/AWS-RunShellScript"
                                        ),
                                        _sub(
                                            "arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}"
                                            ":instance/${Instance}"
                                        ),
                                    ],
                                },
                                {
                                    "Sid": "FollowTheCommand",
                                    "Effect": "Allow",
                                    "Action": [
                                        "ssm:GetCommandInvocation",
                                        "ssm:ListCommandInvocations",
                                    ],
                                    "Resource": ["*"],
                                },
                            ],
                        },
                    }
                ],
                "Tags": _tags("deploy"),
            },
        },
    }


def _edge(contract: dict) -> dict:
    origin = contract["origin"]
    headers_policy = contract["response_headers_policies"]["motionly-edge-security"]
    hsts = headers_policy["strict_transport_security"]

    def behavior(entry: dict) -> dict:
        rendered = {
            "TargetOriginId": origin["id"],
            "ViewerProtocolPolicy": entry["viewer_protocol_policy"],
            "AllowedMethods": entry["allowed_methods"],
            "CachedMethods": entry["cached_methods"],
            "Compress": entry["compress"],
            "CachePolicyId": contract["cache_policies"][entry["cache_policy"]]["managed_id"],
            "OriginRequestPolicyId": contract["origin_request_policies"][
                entry["origin_request_policy"]
            ]["managed_id"],
            "ResponseHeadersPolicyId": {"Ref": "EdgeSecurityHeaders"},
        }
        if entry["path_pattern"] != "*":
            rendered = {"PathPattern": entry["path_pattern"], **rendered}
        return rendered

    behaviors = [behavior(entry) for entry in contract["behaviors"]]
    default = behaviors.pop()
    custom_origin = {
        "HTTPPort": origin["http_port"],
        "OriginProtocolPolicy": origin["protocol_policy"],
        "OriginReadTimeout": origin["response_timeout_seconds"],
        "OriginKeepaliveTimeout": origin["keepalive_timeout_seconds"],
    }
    # The instance's public DNS name for its Elastic IP: ec2-a-b-c-d.<region>.compute.amazonaws.com.
    domain = {
        "Fn::Join": [
            "",
            [
                "ec2-",
                {"Fn::Join": ["-", {"Fn::Split": [".", {"Ref": "OriginAddress"}]}]},
                f".{REGION}.compute.amazonaws.com",
            ],
        ]
    }
    return {
        "EdgeSecurityHeaders": {
            "Type": "AWS::CloudFront::ResponseHeadersPolicy",
            "Properties": {
                "ResponseHeadersPolicyConfig": {
                    "Name": _sub("motionly-${Environment}-edge-security"),
                    "Comment": "Security headers; pages keep their own per-request CSP.",
                    "SecurityHeadersConfig": {
                        "StrictTransportSecurity": {
                            "AccessControlMaxAgeSec": hsts["max_age_seconds"],
                            "IncludeSubdomains": hsts["include_subdomains"],
                            "Preload": hsts["preload"],
                            "Override": hsts["override"],
                        },
                        "ContentTypeOptions": {
                            "Override": headers_policy["content_type_options"]["override"]
                        },
                        "FrameOptions": {
                            "FrameOption": headers_policy["frame_options"]["value"],
                            "Override": headers_policy["frame_options"]["override"],
                        },
                        "ReferrerPolicy": {
                            "ReferrerPolicy": headers_policy["referrer_policy"]["value"],
                            "Override": headers_policy["referrer_policy"]["override"],
                        },
                    },
                    "RemoveHeadersConfig": {
                        "Items": [{"Header": name} for name in headers_policy["remove_headers"]]
                    },
                }
            },
        },
        "Distribution": {
            "Type": "AWS::CloudFront::Distribution",
            "Properties": {
                "DistributionConfig": {
                    "Enabled": contract["distribution"]["enabled"],
                    "Comment": _sub("Motionly ${Environment}"),
                    "PriceClass": contract["distribution"]["price_class"],
                    "HttpVersion": contract["distribution"]["http_versions"],
                    "IPV6Enabled": contract["distribution"]["ipv6"],
                    "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
                    "Origins": [
                        {
                            "Id": origin["id"],
                            "DomainName": domain,
                            "ConnectionAttempts": origin["connection_attempts"],
                            "ConnectionTimeout": origin["connection_timeout_seconds"],
                            "CustomOriginConfig": custom_origin,
                            "OriginCustomHeaders": [
                                {
                                    "HeaderName": header["name"],
                                    "HeaderValue": {"Ref": "OriginVerifySecret"},
                                }
                                for header in origin["custom_headers"]
                            ],
                        }
                    ],
                    "CacheBehaviors": behaviors,
                    "DefaultCacheBehavior": default,
                    "CustomErrorResponses": [
                        {
                            "ErrorCode": code,
                            "ErrorCachingMinTTL": contract["error_caching"]["min_ttl_seconds"],
                        }
                        for code in contract["error_caching"]["status_codes"]
                    ],
                },
                "Tags": _tags("edge"),
            },
        },
    }


def render(contract: dict) -> dict:
    assert contract["schema_version"] == 1, "unsupported edge contract"
    assert contract["distribution"]["viewer_certificate"] == "cloudfront-default"
    assert contract["origin"]["response_completion_timeout_seconds"] is None
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Motionly: CloudFront in front of one EC2 origin in Mumbai, S3 for "
        "releases, media and backups, and a GitHub OIDC deploy role (SSM Run Command, no SSH). "
        "Rendered from infra/aws/edge-contract.json by scripts/aws/render_template.py.",
        "Parameters": _parameters(contract),
        "Mappings": {
            "ComputeProfiles": {
                "gpu": {"InstanceType": "g6.12xlarge"},
                "cpu": {"InstanceType": "m7i.2xlarge"},
            }
        },
        "Conditions": {
            "UseGpu": {"Fn::Equals": [{"Ref": "ComputeProfile"}, "gpu"]},
            "CreateGitHubOidcProvider": {"Fn::Equals": [{"Ref": "GitHubOidcProviderArn"}, ""]},
        },
        "Rules": {
            "MumbaiOnly": {
                "Assertions": [
                    {
                        "Assert": {"Fn::Equals": [{"Ref": "AWS::Region"}, REGION]},
                        "AssertDescription": "Motionly runs in ap-south-1 (Mumbai).",
                    }
                ]
            }
        },
        "Resources": {**_network(), **_storage(), **_instance(), **_deploy(), **_edge(contract)},
        "Outputs": {
            "PublicOrigin": {
                "Description": "PUBLIC_ORIGIN for the release (https://<distribution>.cloudfront.net).",
                "Value": _sub("https://${Distribution.DomainName}"),
            },
            "DistributionId": {"Value": {"Ref": "Distribution"}},
            "InstanceId": {"Value": {"Ref": "Instance"}},
            "ReleasesBucketName": {"Value": {"Ref": "ReleasesBucket"}},
            "MediaBucketName": {"Value": {"Ref": "MediaBucket"}},
            "BackupsBucketName": {"Value": {"Ref": "BackupsBucket"}},
            "GitHubDeployRoleArn": {"Value": {"Fn::GetAtt": ["GitHubDeployRole", "Arn"]}},
        },
    }


def rendered_text(contract: dict) -> str:
    return json.dumps(render(contract), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the template is out of date")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    text = rendered_text(json.loads(CONTRACT_PATH.read_text(encoding="utf-8")))
    if args.check:
        current = TEMPLATE_PATH.read_text(encoding="utf-8") if TEMPLATE_PATH.exists() else ""
        if current != text:
            print(
                f"{TEMPLATE_PATH.relative_to(ROOT)} is out of date; run {Path(__file__).name}",
                file=sys.stderr,
            )
            return 1
        print("render template: up to date")
        return 0
    TEMPLATE_PATH.write_text(text, encoding="utf-8")
    print(f"render template: wrote {TEMPLATE_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
