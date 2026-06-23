# Pre-launch review evidence

Phase 10 launch approval requires current evidence for each review area below. The CI pre-launch
gate verifies this file exists and still names each required area so the release cannot silently
drop non-code obligations.

## Accessibility

- Keyboard editing: editor navigation, upload, caption edit, export, billing, and publish flows are
  covered by browser E2E selectors that use roles and labels.
- Screen-reader semantics: primary controls must expose role/name via labels or visible text.
- Contrast: launch sign-off must include light/dark theme contrast review for dashboard, editor,
  billing, and dialogs.
- Reduced motion: motion-heavy preview/effect controls must respect reduced-motion settings.

## Browser and device compatibility

- Desktop Chromium is enforced in CI.
- Manual launch sign-off must include latest stable Chrome, Safari, Firefox, and mobile Safari.
- Minimum supported viewport is 390px wide; desktop editor target is 1440px wide.

## Privacy and legal

- Source video, transcript, generated asset, analytics, and export retention are documented in
  `docs/data-retention.md`.
- Social tokens are encrypted at rest and refresh paths are tested.
- Provider data processing, stock media licensing, payment terms, privacy policy, cookie policy, and
  deletion request process require legal approval before real-user launch.

## Last review record

- Status: pre-launch technical gate implemented; legal/manual compatibility approvals required
  before production launch.
- Owner: Motionly release owner.
- Review date: 2026-06-23.
