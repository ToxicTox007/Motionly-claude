/**
 * The document title. React hoists a rendered <title> into <head> and keeps
 * it there while this component is mounted, so the title survives streaming,
 * server-action redirects and client-side navigation. Every page renders one,
 * directly or through AppShell or LegalPage (tests/page-titles.test.mjs).
 */
export function PageTitle({ title }: { title?: string }) {
  return <title>{title ? `${title} · Motionly` : "Motionly"}</title>;
}
