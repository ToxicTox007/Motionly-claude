# Temporal self-hosted mTLS assets

`config/production.yaml.tmpl` is rendered only at server startup. It requires frontend and internode mTLS. No certificates, keys, or rendered configuration belong in Git.

The release agent base64-decodes the following Key Vault values directly into mode-0600 Docker secret files: `temporal-ca-cert`, `temporal-server-cert`, `temporal-server-key`, `temporal-internode-cert`, `temporal-internode-key`, and a cert/key pair for each identity below. PEM values must be base64-encoded as a single Key Vault value; the release agent never passes decoded values by argv or logs them.

| Certificate common name | Docker secret files | Temporal authorization |
| --- | --- | --- |
| `motionly-api` | `temporal_api_cert`, `temporal_api_key` | configured namespace read/write |
| `motionly-dispatcher` | `temporal_dispatcher_cert`, `temporal_dispatcher_key` | configured namespace read/write |
| `motionly-temporal-worker` | `temporal_worker_cert`, `temporal_worker_key` | worker RPCs plus configured namespace read |
| `motionly-reconciler` | `temporal_reconciler_cert`, `temporal_reconciler_key` | configured namespace read/write |
| `motionly-namespace-bootstrap` | `temporal_namespace_bootstrap_cert`, `temporal_namespace_bootstrap_key` | operator/admin only until `TEMPORAL_BOOTSTRAP_ADMIN_UNTIL` |
| `motionly-diagnostics` | `temporal_diagnostics_cert`, `temporal_diagnostics_key` | mTLS health and safe diagnostic RPCs only |

The custom server wrapper is pinned to `go.temporal.io/server v1.27.1`. It maps only these common names from `AuthInfo.TLSSubject` and denies absent/unknown certificate identities, other namespaces, and operator/admin APIs except the bounded bootstrap identity.
