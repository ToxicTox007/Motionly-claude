# Vulture whitelist for apps/api (Task 32 deadcode stage).
#
# Vulture cannot see names a framework calls by convention rather than by an
# in-code reference. This file lists ONLY those: each entry is a real name
# reached at runtime by FastAPI / Pydantic / Temporal / pytest that vulture
# would otherwise report as dead. Keep it minimal and reviewed — it is NOT a
# place to silence genuinely dead code.
#
# The deadcode stage runs:
#     vulture app scripts/verify/vulture_whitelist.py --min-confidence 80
# At --min-confidence 80 vulture already discounts decorated route handlers,
# pydantic validators, Temporal activities/workflows and pytest fixtures below
# the threshold, so this list only pins the residual ≥80%-confidence false
# positives. Entries use vulture's own whitelist syntax (a bare name reference,
# as produced by `vulture --make-whitelist`). GPU-only symbols are exempted
# separately by tests/gpu-stage-allowlist.json, not here.

# FastAPI Header parameter declared on app/api/billing.py::handle_gateway_webhook
# for the documented mock-gateway webhook contract (X-Mock-Gateway-Event). FastAPI
# parses and validates it; the handler reads the companion event-id/timestamp
# headers but not the event-type header, so vulture sees the local as unused.
x_mock_gateway_event  # noqa: F821  (vulture whitelist: bare-name reference, not a real binding)
