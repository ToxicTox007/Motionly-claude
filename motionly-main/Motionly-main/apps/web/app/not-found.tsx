import { ButtonLink } from "@/components/button";
import { PixelIcon } from "@/components/pixel-icons";
import { PageTitle } from "@/components/page-title";

export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-6 py-16">
      <PageTitle title="Page not found" />
      <div className="w-full max-w-md rounded-neu-xl bg-surface p-8 text-center shadow-neu-raised-lg">
        <div className="mx-auto mb-6 flex size-16 items-center justify-center rounded-neu-md bg-surface text-primary shadow-neu-inset">
          <PixelIcon name="warning" className="size-7" />
        </div>
        <p className="text-xs font-semibold text-primary">Error 404</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-foreground">
          Page not found
        </h1>
        <p className="mt-3 text-sm font-medium leading-relaxed text-muted-foreground">
          This page could not be found. The link may be broken or the page may
          have moved.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <ButtonLink href="/">Back to home</ButtonLink>
          <ButtonLink href="/dashboard" variant="secondary">
            Go to dashboard
          </ButtonLink>
        </div>
      </div>
    </main>
  );
}
