"""Tests for ``aws_sigv4`` against AWS's published SigV4 test-suite vectors.

Source: the AWS Signature Version 4 test suite / worked example documented at
https://docs.aws.amazon.com/IAM/latest/UserGuide/create-signed-request.html and
the historical "aws-sig-v4-test-suite" ``get-vanilla`` case. The shared fixed
inputs are:

* access key    ``AKIDEXAMPLE``
* secret key    ``wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY``
* region        ``us-east-1``
* service       ``service``
* timestamp     ``20150830T123600Z`` (date stamp ``20150830``)

These are public, non-secret example credentials published by AWS for exactly
this purpose. A representative subset of the suite's worked artifacts is copied
below and checked stage by stage.
"""

from __future__ import annotations

from app.services import aws_sigv4

# --- Published AWS test-suite inputs (get-vanilla) -------------------------
ACCESS_KEY = "AKIDEXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
REGION = "us-east-1"
SERVICE = "service"
AMZ_DATE = "20150830T123600Z"
DATE_STAMP = "20150830"

# get-vanilla signs only host and x-amz-date.
GET_VANILLA_HEADERS = {
    "host": "example.amazonaws.com",
    "x-amz-date": AMZ_DATE,
}

# --- Published AWS test-suite expected artifacts (get-vanilla) -------------
EXPECTED_CANONICAL_REQUEST = (
    "GET\n"
    "/\n"
    "\n"
    "host:example.amazonaws.com\n"
    "x-amz-date:20150830T123600Z\n"
    "\n"
    "host;x-amz-date\n"
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)
EXPECTED_STRING_TO_SIGN = (
    "AWS4-HMAC-SHA256\n"
    "20150830T123600Z\n"
    "20150830/us-east-1/service/aws4_request\n"
    "bb579772317eb040ac9ed261061d46c1f17a8133879d6129b6e1c25292927e63"
)
EXPECTED_SIGNATURE = "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"


def test_canonical_request_matches_published_vector() -> None:
    canonical = aws_sigv4.build_canonical_request(
        method="GET",
        path="/",
        query=None,
        headers=GET_VANILLA_HEADERS,
        payload_hash=aws_sigv4.EMPTY_PAYLOAD_HASH,
    )
    assert canonical.canonical_request == EXPECTED_CANONICAL_REQUEST
    assert canonical.signed_headers == "host;x-amz-date"


def test_string_to_sign_matches_published_vector() -> None:
    canonical = aws_sigv4.build_canonical_request(
        method="GET",
        path="/",
        query=None,
        headers=GET_VANILLA_HEADERS,
        payload_hash=aws_sigv4.EMPTY_PAYLOAD_HASH,
    )
    scope = aws_sigv4.credential_scope(
        date_stamp=DATE_STAMP, region=REGION, service=SERVICE
    )
    string_to_sign = aws_sigv4.build_string_to_sign(
        amz_date=AMZ_DATE,
        scope=scope,
        hashed_canonical_request=canonical.hashed_canonical_request,
    )
    assert string_to_sign == EXPECTED_STRING_TO_SIGN


def test_signature_matches_published_vector() -> None:
    canonical = aws_sigv4.build_canonical_request(
        method="GET",
        path="/",
        query=None,
        headers=GET_VANILLA_HEADERS,
        payload_hash=aws_sigv4.EMPTY_PAYLOAD_HASH,
    )
    scope = aws_sigv4.credential_scope(
        date_stamp=DATE_STAMP, region=REGION, service=SERVICE
    )
    string_to_sign = aws_sigv4.build_string_to_sign(
        amz_date=AMZ_DATE,
        scope=scope,
        hashed_canonical_request=canonical.hashed_canonical_request,
    )
    signing_key = aws_sigv4.derive_signing_key(
        secret_access_key=SECRET_KEY,
        date_stamp=DATE_STAMP,
        region=REGION,
        service=SERVICE,
    )
    signature = aws_sigv4.calculate_signature(
        signing_key=signing_key, string_to_sign=string_to_sign
    )
    assert signature == EXPECTED_SIGNATURE


def test_get_vanilla_query_canonical_query_string() -> None:
    # get-vanilla-query: a single query parameter ``Param1=value1``. The
    # canonical query string is the sorted, percent-encoded key=value list.
    assert (
        aws_sigv4.canonical_query_string({"Param1": "value1"}) == "Param1=value1"
    )
    # Reserved characters in values are percent-encoded; keys are sorted.
    assert (
        aws_sigv4.canonical_query_string({"b": "+", "a": "x y"}) == "a=x%20y&b=%2B"
    )


def test_sign_request_builds_authorization_header_with_scope() -> None:
    # The high-level helper adds x-amz-content-sha256 (an AWS-recommended signed
    # header) and the Authorization header. It is deterministic given amz_date.
    signed = aws_sigv4.sign_request(
        credentials=aws_sigv4.SigV4Credentials(
            access_key_id=ACCESS_KEY, secret_access_key=SECRET_KEY
        ),
        method="POST",
        host="bedrock-runtime.us-east-1.amazonaws.com",
        path="/model/test-model/converse",
        region=REGION,
        service="bedrock",
        amz_date=AMZ_DATE,
        payload=b'{"x":1}',
    )
    assert signed.authorization.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "20150830/us-east-1/bedrock/aws4_request" in signed.authorization
    assert "SignedHeaders=" in signed.authorization
    assert "host" in signed.headers["authorization"]
    assert signed.headers["x-amz-content-sha256"]
    # Signing the same inputs twice is stable.
    again = aws_sigv4.sign_request(
        credentials=aws_sigv4.SigV4Credentials(
            access_key_id=ACCESS_KEY, secret_access_key=SECRET_KEY
        ),
        method="POST",
        host="bedrock-runtime.us-east-1.amazonaws.com",
        path="/model/test-model/converse",
        region=REGION,
        service="bedrock",
        amz_date=AMZ_DATE,
        payload=b'{"x":1}',
    )
    assert again.signature == signed.signature


def test_session_token_is_signed() -> None:
    signed = aws_sigv4.sign_request(
        credentials=aws_sigv4.SigV4Credentials(
            access_key_id=ACCESS_KEY,
            secret_access_key=SECRET_KEY,
            session_token="FQoGZXIvYXdzEBYa.EXAMPLE",
        ),
        method="GET",
        host="example.amazonaws.com",
        path="/",
        region=REGION,
        service=SERVICE,
        amz_date=AMZ_DATE,
    )
    assert signed.headers["x-amz-security-token"] == "FQoGZXIvYXdzEBYa.EXAMPLE"
    assert "x-amz-security-token" in signed.authorization
