# Editor collaboration contract (v1)

Motionly v1 uses whole-document optimistic concurrency for the canonical timeline.

- Each shared editor manifest has one integer `revision`.
- A writer submits the complete manifest with the `base_revision` it read.
- The server accepts the write only when `base_revision` equals the current revision, then increments it.
- A stale write receives HTTP 409. The client reloads the current manifest and the user must reapply the conflicting edit.
- Presence and update broadcasts are awareness and refresh signals; they do not merge fields.
- There is no CRDT, operational transform, field-level merge, or conflict-free offline editing in v1.

This keeps timeline/render authority deterministic. Field-level collaborative merging requires a separately versioned protocol and is outside the v1 contract.
