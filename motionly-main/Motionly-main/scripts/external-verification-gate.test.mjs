import assert from "node:assert/strict";
import test from "node:test";
import { verifyExternalVerification } from "./external-verification-gate.mjs";

const authorized = {
  EXTERNAL_VERIFICATION_AUTHORIZATION: "protected-approval-token",
  EXTERNAL_VERIFICATION_TARGET: "staging-sandbox",
  EXTERNAL_VERIFICATION_RUN_ID: "run-123",
  EXTERNAL_VERIFICATION_ALLOWED_HOSTS: "canary.sandbox.motionly.test, provider.sandbox.motionly.test",
};

test("external verification fails closed without protected authorization", () => {
  assert.throws(
    () => verifyExternalVerification({
      environment: { ...authorized, EXTERNAL_VERIFICATION_AUTHORIZATION: "" },
      endpoints: ["https://canary.sandbox.motionly.test/promote"],
    }),
    /EXTERNAL_VERIFICATION_AUTHORIZATION/,
  );
});

test("external verification requires its staging sandbox target and exact allowlisted HTTPS hosts", () => {
  assert.throws(
    () => verifyExternalVerification({
      environment: { ...authorized, EXTERNAL_VERIFICATION_TARGET: "production" },
      endpoints: ["https://canary.sandbox.motionly.test/promote"],
    }),
    /staging-sandbox/,
  );
  assert.throws(
    () => verifyExternalVerification({
      environment: authorized,
      endpoints: ["https://untrusted.example.test/promote"],
    }),
    /not allowlisted/,
  );
  assert.throws(
    () => verifyExternalVerification({
      environment: authorized,
      endpoints: ["http://canary.sandbox.motionly.test/promote"],
    }),
    /HTTPS/,
  );
});

test("external verification evidence never contains its authorization value", () => {
  const evidence = verifyExternalVerification({
    environment: authorized,
    endpoints: [
      "https://canary.sandbox.motionly.test/promote?token=not-exported",
      "https://provider.sandbox.motionly.test/contracts",
    ],
  });

  assert.deepEqual(evidence, {
    schema_version: 1,
    target: "staging-sandbox",
    run_id: "run-123",
    endpoint_hosts: ["canary.sandbox.motionly.test", "provider.sandbox.motionly.test"],
  });
  assert.doesNotMatch(JSON.stringify(evidence), /protected-approval-token|not-exported/);
});
