import Link from "next/link";
import type { ReactNode } from "react";
import { Badge } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { PageTitle } from "@/components/page-title";

type LegalSection = {
  title: string;
  // A plain string (the common case) or rich, accessible markup (e.g. a list
  // with a policy link), so a data-driven section can name a policy in link
  // text without the page hard-coding processor names.
  body: ReactNode;
};

export function LegalPage({
  title,
  updated,
  sections,
}: {
  title: string;
  updated: string;
  sections: LegalSection[];
}) {
  return (
    <main className="min-h-screen bg-background">
      <PageTitle title={title} />
      <div className="container-content max-w-3xl py-12 lg:py-16">
        <Link
          href="/"
          className="mb-10 inline-flex min-h-10 items-center gap-2 rounded-neu bg-surface px-4 py-2 text-sm font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed"
        >
          <PixelIcon name="play" className="size-3 rotate-180" />
          Back to Motionly
        </Link>

        <div className="mb-10">
          <Badge variant="primary" className="mb-4">
            Last updated {updated}
          </Badge>
          <h1 className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
            {title}
          </h1>
        </div>

        <div className="divide-y divide-hairline overflow-hidden rounded-neu-lg bg-surface shadow-neu-raised">
          {sections.map((section) => {
            const id = section.title
              .toLowerCase()
              .replace(/[^a-z0-9]+/g, "-")
              .replace(/^-|-$/g, "");
            return (
              <section
                key={section.title}
                id={id}
                className="px-6 py-7 sm:px-8"
              >
                <h2 className="mb-3 text-lg font-bold text-foreground">
                  {section.title}
                </h2>
                {typeof section.body === "string" ? (
                  <p className="text-sm font-medium leading-[1.8] text-muted-foreground">
                    {section.body}
                  </p>
                ) : (
                  <div className="text-sm font-medium leading-[1.8] text-muted-foreground">
                    {section.body}
                  </div>
                )}
              </section>
            );
          })}
        </div>

        <div className="mt-12 border-t border-hairline pt-8 text-center">
          <Link
            href="/"
            className="inline-flex min-h-10 items-center gap-2 rounded-neu bg-surface px-4 py-2 text-sm font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed"
          >
            <PixelIcon name="play" className="size-3 rotate-180" />
            Return to Motionly
          </Link>
        </div>
      </div>
    </main>
  );
}
