# Temporal mTLS/AuthZ staged same-VM canary

This runbook is a **gate**, not a deployment procedure. No live canary has been run by this repository. The release agent refuses automatic Temporal activation or migration until an approved operator supplies an overlap-proof file to `scripts/deploy/temporal_canary_gate.sh`.

## Why the gate is required

The custom `MOTIONLY_TEMPORAL_SERVER_IMAGE` changes the Temporal frontend security mode from plaintext/no-op authorization to required mTLS and an explicit authorizer. A same-VM dual frontend is not assumed safe: it can bind the same frontend port, duplicate membership, or split traffic. Do not claim zero downtime until the exact Temporal version, ports, membership, and frontend overlap topology have been independently proven for the target VM.

## Required evidence

An approved, root-owned mode-0600 proof file must contain exactly these affirmative lines after a controlled canary:

```text
signed_release_verified=true
preflight_verified=true
mtls_handshake_verified=true
authorization_role_matrix_verified=true
controlled_namespace_validation_verified=true
rollback_verified=true
dual_frontend_overlap_verified=true
```

The operator must independently verify: the signed manifest and archive; VM preflight; each mTLS identity handshake; allow/deny outcomes for `api`, `dispatcher`, `temporal-worker`, `reconciler`, `namespace-bootstrap`, and `diagnostics`; a read-only or controlled namespace validation; and a tested rollback to the prior signed release. `namespace-bootstrap` must use a short-lived certificate and `TEMPORAL_BOOTSTRAP_ADMIN_UNTIL` no more than 24 hours ahead.

Run only the evidence validator after those checks:

```bash
scripts/deploy/temporal_canary_gate.sh \
  --release-dir /var/lib/motionly/releases/<git-sha> \
  --overlap-proof /etc/motionly/temporal-overlap-proof.env
```

The script never runs Compose, Docker, cloud, or migration commands. Missing or incomplete proof deliberately blocks activation. Roll back with the existing signed-release rollback command only after the controlled validation demonstrates a failure; do not bypass the gate with plaintext Temporal.
