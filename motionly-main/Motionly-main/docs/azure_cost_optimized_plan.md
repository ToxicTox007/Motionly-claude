# Deprecated Azure hybrid worker plan

This historical Railway/Vercel hybrid deployment plan is retired. Motionly production runs
only the digest-pinned Docker Compose topology on one Azure VM; see
[`docs/ops/compose-vm-deployment.md`](ops/compose-vm-deployment.md). Do not deploy workers,
Redis, the API, or the web application through the retired hybrid or managed-cluster platforms.
