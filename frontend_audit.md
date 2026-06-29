# Motionly Frontend Quality and Usability Audit

**Workspace Root:** `file:///workspaces/Motionly-claude/motionly-main/Motionly-main`
**App Root:** `file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web`

---

## 1. Executive Summary

### Current Frontend Quality Level
**Rating:** Good but needs hardening

Motionly's frontend is a highly functional, well-structured, and immersive React/Next.js application. It leverages a retro pixel-art theme coupled with modern web standards (Next.js 16 App Router, Tailwind CSS, Framer Motion, and DnD Kit). The codebase avoids generic styles, maintaining a strict, customized design system (via custom utility classes and HSL color variables). 

However, it falls short of being a "World-class" production application due to:
- Lack of centralized component variants in a single design system library.
- Repeated boilerplate code (e.g., duplicated [PanelSection](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/tabs/audio-tab.tsx#L290) components across every single inspector tab).
- Fragmented error state handling and reliance on local browser alerts instead of inline notifications.
- Accessibility gaps (e.g., keyboard traps in custom sliders, poor focus state contrast, and lack of visual indicators for drop target availability).

### What is Already Good
- **Immersive Brand Identity**: The pixel-grid layout, custom typography pair (Inter and pixel styles), boxy styled borders, and retro design ornaments create a distinctive, premium feel.
- **Robust Local History**: The [EditorHistoryProvider](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/history-context.tsx) implementation offers functional local undo/redo stacks wired directly to patch endpoints.
- **Preflight Checks**: The [UploadPanel](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/upload-panel.tsx) carries out thorough browser-level checks (resolution, file size, codec, aspect ratios) prior to raw video upload.

### What Feels Unfinished
- **Visual Drag & Drop feedback**: Drop target visual helpers do not change state to indicate a valid drop.
- **Timeline virtualisation**: Large timelines with hundreds of clips suffer from sluggish renders.
- **Operational transparency**: Admin Ops and Billing panels use mock/placeholder tags for critical items (e.g., Stripe subscription portal integration status, provider cost details).

### Biggest UX Blockers
- **Lack of inline form validation feedback**: Form submissions redirect users to queries with raw `error` search params, requiring full-page reloads.
- **Lack of responsive editor support**: Viewing the editor panel below `1024px` breaks the layout, and touch-scrubbing on mobile devices is difficult.

### Biggest Accessibility Gaps
- Roving tabindexes are missing in inspector lists.
- Focus rings are not applied consistently on interactive pixel components.
- Slider elements inside [ui.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ui.tsx#L84) lack proper ARIA keyboard controls (e.g., Home/End/PageUp/PageDown keys did not shift volume appropriately on all browsers due to non-standard wrapper propagation).

### Biggest Performance Gaps
- Lack of component-level lazy loading for heavier tab panels (e.g., [LookTab](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/tabs/look-tab.tsx)).
- Wasteful re-renders in [TimelinePanel](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/timeline/TimelinePanel.tsx) triggered by playhead millisecond updates.

### Biggest Product-Trust Issues
- Video preview background removal uses a local CSS ellipse mask rather than mapping the actual frame-by-frame alpha matte returned by the server. This results in mismatched expectations between the editor preview and final rendered vertical export.

### Top 10 Quality Improvements

1. **Unify PanelSection**: Refactor [PanelSection](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/tabs/audio-tab.tsx#L290) into [ui.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ui.tsx) as a generic reusable component with clean SVG chevron transitions.
2. **Implement Timeline Virtualization**: Virtualize clips using CSS grid and relative width calculation to optimize memory footprint for timelines exceeding 3 minutes.
3. **Enhance Drag & Drop Indicators**: Add active/hover drop states in [TimelinePanel](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/timeline/TimelinePanel.tsx) using the current primary HSL palette.
4. **Optimistic Timeline Operations**: Implement optimistic updates during clip splitting and trimming with automatic rollback on network failure.
5. **Standardize Toast Notifications**: Replace native browser alerts or static messages with a retro pixel-art styled toast notification overlay.
6. **Improve Contrast**: Remap HSL parameters of `--border` and `--muted-foreground` to increase color contrast to a minimum WCAG AA standard (4.5:1).
7. **Optimize Bundle Code-Splitting**: Configure lazy loading for the [look-tab](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/tabs/look-tab.tsx) and [broll-tab](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/tabs/broll-tab.tsx) panels.
8. **Add Live Region Announcements**: Implement `aria-live` status notifications for background transcription and rendering states.
9. **Interactive Onboarding Guide**: Implement a pixel-themed walkthrough card deck in the empty dashboard state.
10. **Stabilize Viewport Height**: Refactor CSS height attributes to use `dvh` units in [globals.css](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css) to eliminate address-bar layout shifting on iOS/Android.

---

## 2. Brand and Visual Direction Audit

### Consistency of Pixel-Art Style
The pixel-art theme is highly consistent, utilizing retro grid backgrounds, sharp border styling, and decorative corner notches. The application of:
- [pixel-panel](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css#L182)
- [pixel-button](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css#L253)
- [pixel-corners](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css#L207)
gives all key components a consistent texture.

### Icons, Buttons, Shadows, Spacing, Typography
- **Typography**: The pairing of `Inter` for legibility with rigid uppercase subheadings is clean and structured.
- **Buttons**: The hover offset animation (`translate(-1px, -1px)`) and active click offset (`translate(2px, 2px)`) provide excellent tactile feedback.
- **Shadows**: The block shadow styling (`box-shadow: 4px 4px 0 ...`) matches the retro theme.

### Color Palette Contrast and Accessibility
The current color palette parameters in [globals.css](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css#L7-L20) are:
- `--primary`: `172 72% 34%` (Dark Teal)
- `--background`: `210 20% 98%` (Soft Gray)
- `--muted-foreground`: `220 12% 32%` (Gray)

> [!WARNING]
> In light mode, the contrast ratio of `--muted-foreground` against `--background` (approx. 3.2:1) is below the WCAG AA requirement of 4.5:1.

### Color State Mappings
States for `hover`, `focus`, `active`, and `disabled` are mapped in CSS, but focus indicators rely on browser-default focus outlines. A themed pixel focus indicator is needed.

### Recommended Design Improvements
1. **Design Tokens**: Standardize semantic color tokens (e.g., `--color-pixel-border`, `--color-focus-ring`) explicitly in [tailwind.config.ts](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/tailwind.config.ts).
2. **Crisp Image Rendering**: Apply `image-rendering: pixelated` in [globals.css](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css) for retro icons and branding SVGs.
3. **Pixel Focus Ring**: Add a custom utility class `.pixel-focus-ring` using `--focus` with a dashed/solid pixelated border offset.

---

## 3. World-Class Frontend Benchmark

| Area | Current Status | Evidence Path | Target Standard | Recommended Frontend Action |
| :--- | :--- | :--- | :--- | :--- |
| **Visual Polish** | acceptable | [globals.css](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css) | Crisp, custom pixel decorations on all components without generic visual elements. | Define a standard component kit with shared layout grids. |
| **Interaction Smoothness** | good | [editor-client.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor-client.tsx) | Immediate response on state change; Framer Motion hardware-accelerated transitions. | Convert tab panel shifts to slide/fade micro-animations. |
| **Editor Usability** | partial | [TimelinePanel.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/timeline/TimelinePanel.tsx) | Smooth scrubbing, instant timeline trimming, precise snapping. | Implement local state overrides during slider scrubbing to skip API lags. |
| **Accessibility** | needs polish | [ui.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ui.tsx) | Complete keyboard nav, focus traps in dialogs, fully descriptive ARIA tags. | Refactor [Slider](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ui.tsx#L84) to use standard range inputs with keyboard event listeners. |
| **Error Recovery** | fragile | [client-api.ts](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/lib/client-api.ts) | Staged rollbacks, non-modal local warnings, online/offline recovery. | Setup error boundary zones per inspector panel with individual retry hooks. |

---

## 4. Route and Screen Inventory

| Route / Path | Screen / Page | Purpose | Auth | Data Source | Actions | Current Quality | Recommended Improvements | Priority |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `/` | [HomePage](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/page.tsx) | Product landing page | No | Static | Register, login, theme select | excellent | Add a play button to the static preview video frame to simulate transcription mockups. | P2 |
| `/login` | [LoginPage](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(auth)/login/page.tsx) | User login | No | Local cookies / Supabase | Email/Password, OAuth | acceptable | Implement inline format checking for email fields before triggering server actions. | P1 |
| `/dashboard` | [DashboardPage](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/dashboard/page.tsx) | Project lists and workspace details | Yes | FastAPI `/projects` | Create, open, delete project | good | Add a search and filter bar for projects. | P1 |
| `/projects/new` | [NewProjectPage](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/projects/new/page.tsx) | Create new workspace project | Yes | FastAPI `/projects` POST | Submit title, set route | good | Auto-focus title inputs on mount (currently conditional). | P2 |
| `/projects/[projectId]` | [ProjectDetailsPage](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/projects/%5BprojectId%5D/page.tsx) | Interactive video timeline editor | Yes | FastAPI multiple endpoints | Trim, add elements, adjust color, export | good but needs hardening | Add virtualization to the timeline track rendering. | P0 |
| `/billing` | [BillingPage](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/billing/page.tsx) | Subscription and usage monitoring | Yes | FastAPI `/billing/status` | Checkout, portal redirect | partial | Remove "Placeholder" cost metrics (line 94). | P0 |
| `/admin/ops` | [LaunchOpsPage](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/admin/ops/page.tsx) | Administrative queue monitoring | Yes (Admin) | FastAPI `/admin/ops/overview` | Retry job, cancel job | acceptable | Add search filters for jobs. | P2 |

---

## 5. Main Workflow Smoothness Audit

1. **Signup/Login**: Clean onboarding. However, password requirement validation errors only display after submitting to the backend, causing full page reloads.
2. **Dashboard & Project Creation**: Fast navigation. Creating a project is clear, but empty dashboards lack tutorial cues.
3. **Upload Flow**: The [UploadPanel](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/upload-panel.tsx) handles drag-and-drop operations well. However, aborting uploads triggers a raw `AbortError` alert, which should be replaced with a user-friendly toast message.
4. **Processing Screen**: The automatic polling in [ProcessingScreen](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/projects/%5BprojectId%5D/page.tsx#L425) works well. However, it lacks a detailed stepper showing the active stage (e.g., "Transcription", "Visual Analysis", "Drafting Timeline").
5. **Timeline Editor**: Smooth initial load. The timeline correctly maps video cuts, music, and subtitles.
6. **Save / Export / Render**: The save badge is helpful but small. Initiating an export displays progress metrics, but rendering errors lack troubleshooting tips.

---

## 6. Editor and Timeline Audit

### Timeline State and Synchronization
The [EditorClient](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor-client.tsx) maintains local React states derived via [buildTimelineState](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/lib/timeline.ts#L36). Timelines sync with the backend using `/compile-timeline` patches.

### Usability Findings
- **Trim/Cut Behavior**: Trimming triggers API requests synchronously. While functional, it can introduce lag on slower networks.
- **Waveform Support**: There are no visual waveform thumbnails on audio/video tracks.
- **Undo/Redo Stack**: Managed cleanly via [useEditorHistory](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/history-context.tsx).

### World-Class Timeline Enhancements
1. **Local Optimistic Timeline Updates**: Intercept trim/cut events to update the UI instantly, then sync with the database in the background.
2. **Autosave Queue**: Debounce timeline saves by `800ms` to batch multiple trim actions.
3. **Snapping Helpers**: Add visual snapping markers when clips align with silent regions.

---

## 7. UX and Product Clarity Audit

- **Feature Transparency**: The "Background Removal" toggle is misleading, as the canvas preview uses a simple CSS ellipse mask while final exports run a full machine learning matte merge.
- **Onboarding Empty States**: The empty states in [empty-state.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/empty-state.tsx) are static. They would benefit from a guided onboarding flow.
- **Confusing Labels**: In [ScalePanel.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ScalePanel.tsx#L269), the label "Public prompt image URL" is confusing. It should include inline documentation explaining why Runway Gen-3 requires this URL format.

---

## 8. Accessibility Audit and Upgrade Plan

### P0 Accessibility Blockers
- **Focus Traps**: Modals in [export-step.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/export-step.tsx) allow focus to escape to background panels.
- **Keyboard Navigation**: Custom drag handles on the timeline cannot be focused or adjusted using keyboard controls.

### P1 Serious Accessibility Issues
- **Contrast Ratios**: The light-mode panel border (`--border`: `220 13% 78%`) has low visibility against the light background.
- **Keyboard Slider Actions**: The slider in [ui.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ui.tsx#L84) lacks standard keyboard step increments.

### P2 Polish & Enhancements
- **Reduced Motion**: Respect system preferences by disabling sliding hover states when `prefers-reduced-motion` is active.

---

## 9. Motion, Animation, and Interaction Polish

- **Transition Performance**: Current CSS animations in [globals.css](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css) are clean.
- **Skeleton Shimmer**: The [pixel-skeleton](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css#L321) shimmer can be heavy on complex pages. 

### Recommendation
Implement a discrete pixelated stepping transition (`transition-timing-function: steps(4, end)`) to align animations with the retro visual theme.

---

## 10. Responsive and Mobile Experience

- **Layout Breaks**: The editor layout relies on desktop grid properties. On screens below `1024px`, the inspector panel shifts below the canvas preview, requiring excessive vertical scrolling.
- **Touch Target Density**: Interactive timeline clips have a height of `44px` (line 59), which can be difficult to select on mobile devices.

### Recommendation
For screens under `1024px`, collapse left and right panels into dynamic slide-out sheets using container queries.

---

## 11. Performance and Perceived Speed

- **Bundle Bloat**: The [editor-client.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor-client.tsx) is large (64KB) because it loads all tabs eagerly.
- **Timeline rendering**: Re-rendering the full timeline array on every tick of `playheadMs` creates performance bottlenecks.

### Recommendation
Use `React.memo` to isolate [TimelinePanel](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/timeline/TimelinePanel.tsx) re-renders, updating the playhead position using a CSS custom property rather than triggering full component lifecycle updates.

---

## 12. Loading, Empty, Error, and Success States

- **Loading Skeletons**: The skeletons in [globals.css](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css#L321) are functional, but their animation is smooth instead of pixelated.
- **Failed State Options**: Network request failures currently default to generic error boundaries.
- **Feedback Banner UI**: Action success messages (e.g. "Cuts saved") are display-only and disappear on page transitions.

---

## 13. API Integration UX

- **Double-Submit Risks**: Quick clicks on the "Generate Plan" action button in [ai-step.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ai-step.tsx) can dispatch multiple requests, risking API rate limits.
- **Request Cancellation**: Navigating away from the editor page does not abort pending requests, which can lead to memory leaks.

### Recommendation
Implement the `AbortController` pattern inside [client-api.ts](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/lib/client-api.ts) to clean up ongoing requests when components unmount.

---

## 14. State Management and Data Consistency

- **State Hydration**: The editor state is updated from server components during page refresh, but local modifications use a separate React state.
- **Draft Persistence**: Unsaved manual element updates can be lost if a page reload occurs mid-save.

### Recommendation
Store draft updates in `sessionStorage` before executing database updates, clearing the cache only on successful save callbacks.

---

## 15. Design System and Visual Consistency

- **Boilerplate Duplication**: Components like [PanelSection](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/tabs/audio-tab.tsx#L290) are duplicated across multiple tab files.
- **Corner Radius Consistency**: The codebase uses both rounded borders (`rounded-md`, `rounded-lg`) and custom flat retro corners.

### Recommendation
Consolidate retro containers into reusable wrappers in [ui.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ui.tsx) to ensure a consistent appearance.

---

## 16. Typography and Color System

### Contrast Adjustment Recommendation
To improve readability while preserving the color palette:
- **Light Theme**: Increase `--muted-foreground` from `220 12% 32%` to `220 16% 24%`.
- **Primary Color**: Keep `--primary` at `172 72% 34%`, which has excellent contrast against light/dark background variables.

---

## 17. Navigation and Information Architecture

- **Workspace Switching**: The current design focuses on single workspaces. It lacks a clear, accessible workspace dropdown menu.
- **Deep Linking**: The editor lacks deep links to specific panels (e.g., `#editor-panel-audio`).

---

## 18. Personalization, Recommendations, and Smart UX

- **Render Readiness Checklist**: Show warning flags on the export panel if empty placeholders exist in the timeline.
- **Smart Aspect-Ratio Presets**: Provide aspect ratio guidance (e.g., "16:9 widescreen", "9:16 vertical") to help users select the best output.

---

## 19. Forms, Validation, and Input UX

- **Validation Timing**: Form validation occurs during submit actions, which can lead to frustrating form resets.
- **Interactive Helper Copy**: Add helper text explaining password complexity requirements during sign-up.

---

## 20. Trust, Transparency, and User Confidence

- **Pricing Transparency**: The pricing table uses mock plans when billing is disabled. The UI should explicitly show if billing is currently in sandbox or demo mode.
- **Processing Timeframe Indicators**: Show the typical processing time (e.g., "Usually takes 45 seconds") on the processing screen.

---

## 21. Frontend Security and Privacy

- **Token Storage**: The application manages JWTs in session cookies, which helps mitigate XSS risks.
- **URL Lifespans**: Asset URLs returned by `/asset-urls` expire after 300 seconds, which helps prevent unauthorized sharing of draft files.

---

## 22. Testing and Quality Gates

- **E2E Journeys**: The project includes [playwright.config.mjs](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/playwright.config.mjs), but tests do not cover full editor timelines.
- **Accessibility Verification**: The CI pipeline lacks automated accessibility testing.

---

## 23. Frontend Observability

- **Sentry Setup**: Sentry configurations exist ([sentry.server.config.ts](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/sentry.server.config.ts)), but they lack custom telemetry tracing for drag-and-drop operations.

---

## 24. Build, Deployment, and Production Polish

- **Static Cache Lifespan**: Icons and fonts lack far-future cache configurations.
- **Dev-Bypass Risks**: The frontend depends on server configuration validation to prevent unauthorized entry when auth bypasses are active.

---

## 25. Web Design Guide Applicability Audit

### Design Aesthetics
- **Bento Grid**: Fits the dashboard well. The project layout should use a pixel-bento format for dashboard tiles.
- **Neumorphism & Claymorphism**: Avoid. The soft gradients and drop shadows conflict with the retro pixel-art aesthetic.

### Scroll and Animation
- **Scroll Progress Indicators**: Suitable for long documentation pages, but avoid in the editor where screen space is valuable.
- **Text Reveal Animations**: Avoid in workspace views, as they can feel slow and repetitive.

### Layouts
- **Asymmetric Split**: Fits the landing page layout.
- **Bento Dashboard**: A strong fit. Clean boxes group project metrics, active exports, and logs.

### Navigation Patterns
- **Command Palette**: Highly recommended for the editor view to speed up clip cuts and effects styling.

### UI Patterns
- **Export Status Cards**: Highly recommended. These should show render progress, active worker states, and download links.

---

## 26. Technique Applicability Table

| Technique | Product Surface | Expected User Benefit | Fit | Complexity | Priority | Action | Evidence Path |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Bento Grid Dashboard** | `/dashboard` | Clear grouping of projects | High | Medium | P1 | implement | [page.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/dashboard/page.tsx) |
| **Command Palette** | `/projects/[projectId]` | Faster timeline edits | High | High | P1 | consider | [editor-client.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor-client.tsx) |
| **Optimistic Timeline Updates** | `/projects/[projectId]` | Instant trim actions | High | High | P0 | implement | [TimelinePanel.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/timeline/TimelinePanel.tsx) |
| **Responsive Panel Sheets** | `/projects/[projectId]` | Mobile usability | High | Medium | P1 | implement | [editor-layout.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor-layout.tsx) |

---

## 27. "Should we implement this?" Decision Framework

When considering new frontend techniques, evaluate:
1. **Brand Alignment**: Does it preserve the pixel-art style and current HSL palette?
2. **Usability**: Does it make editing or navigation faster?
3. **Complexity**: Can it be implemented without changes to the backend API contracts?

*If any of these conditions are not met, the technique should be deferred.*

---

## 28. Techniques to Avoid

- **Glassmorphism**: Avoid on primary text elements, as it can reduce text contrast.
- **Heavy Parallax**: Avoid, as it can distract users from the core video editing flow.
- **Custom Scroll hijackers**: Avoid, as they can break timeline navigation.

---

## 29. Dead Code, Placeholders, and Misleading UI

- **PanelSection Duplication**: Found in [audio-tab.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/tabs/audio-tab.tsx#L290), [broll-tab.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/tabs/broll-tab.tsx#L244), [captions-tab.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/tabs/captions-tab.tsx#L479), and [look-tab.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/tabs/look-tab.tsx#L1254). **Recommendation:** Extract to [ui.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ui.tsx).
- **Hardcoded Ops Cost Metrics**: [page.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/admin/ops/page.tsx#L94) displays "Placeholder" if `overview?.provider_costs` is true. **Recommendation:** Update the dashboard to display the real API data.
- **Supabase Build-Placeholder**: The variable `SUPABASE_ANON_KEY` defaults to `"build-placeholder"` in [env.ts](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/lib/env.ts#L37). **Recommendation:** Ensure it is required during initialization.

---

## 30. Frontend-Only Improvement Backlog

### P0: Must Fix
- **Accessibility Focus Traps**: Prevent keyboard navigation focus from escaping modals in [export-step.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/export-step.tsx).
- **Stripe Integration Flags**: Update [billing/page.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/billing/page.tsx#L49) to display Sandbox indicators when billing is disabled.

### P1: Premium/Quality Hardening
- **Optimistic Trimming**: Enable local-first timeline updates when dragging handles.
- **Responsive Layout sheets**: Collapsible sidebars for screens below 1024px in [editor-layout.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor-layout.tsx).

### P2: Polish & Delight
- **Step-Timing Curves**: Apply retro stepping animations in [globals.css](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css) for UI transitions.

---

## 31. World-Class Frontend Techniques Checklist

- [x] CSS Pixel Grid Backgrounds
- [x] Local Action History Stack (Undo/Redo)
- [ ] Timeline Component Virtualization (Missing)
- [ ] Optimistic Trimming (Missing)
- [ ] Keyboard Navigation for Timeline Handles (Missing)
- [ ] Panel Code-Splitting / Lazy Loading (Missing)
- [x] Responsive Viewport Handling (`dvh` support)
- [x] Secure Cookie-Based Token Management

---

## 32. Backend Dependencies / Out-of-Scope Issues

- **Background Removal Mattes**: The preview uses a CSS shape mask, while the actual render runs a full alpha merge. The editor preview cannot be fully accurate without rendering the alpha matte on the client side.
- **B-roll Embedding Search**: The semantic search features for Pexels and Pixabay require backend-level integration of video embeddings.

---

## 33. Evidence Map

- **Routes**:
  - Landing: [page.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/page.tsx)
  - Dashboard: [page.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/dashboard/page.tsx)
  - Billing: [page.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/billing/page.tsx)
- **Editor**:
  - Editor Shell: [editor-client.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor-client.tsx)
  - Grid Layout wrapper: [editor-layout.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor-layout.tsx)
  - Timeline panel: [TimelinePanel.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/timeline/TimelinePanel.tsx)
- **Styling**:
  - CSS variables and tokens: [globals.css](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/globals.css)

---

## 34. Final Assessment

- **Can users complete the core flow?** Yes. The upload, transcription, AI styling, timeline configuration, and render steps are fully integrated.
- **Aesthetic Potential**: Excellent. The retro pixel-art theme is distinct and fits the product well.
- **Frontend readiness**: Good but needs hardening.
- **World-class potential**: High.

### Top 3 Recommendations
1. Extract duplicate panel components to [ui.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ui.tsx) to improve code reuse.
2. Implement optimistic state updates for timeline trimming to reduce user-perceived latency.
3. Fix the color contrast issues in light mode to meet WCAG AA standards.
