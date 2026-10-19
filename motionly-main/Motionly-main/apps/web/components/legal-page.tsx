import Link from "next/link";
import { Badge } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { PageTitle } from "@/components/page-title";

type LegalSection = {
  title: string;
  body: string;
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
    <main className="min-h-screen">
      <PageTitle title={title} />
      <div className="container-content py-12 lg:py-16">
        <Link
          href="/"
          className="mb-10 inline-flex min-h-10 items-center gap-2 rounded border-2 border-foreground bg-surface px-3 py-1.5 text-xs font-black uppercase text-foreground shadow-brutal-sm transition-all duration-[100ms] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-brutal active:translate-x-0 active:translate-y-0 active:shadow-none"
        >
          <PixelIcon name="play" className="size-3 rotate-180" />
          Back to Motionly
        </Link>

        <div className="mb-10">
          <Badge variant="primary" className="mb-4">
            Last updated {updated}
          </Badge>
          <h1 className="text-4xl font-black uppercase text-foreground sm:text-5xl">
            {title}
          </h1>
        </div>

        <div className="overflow-hidden rounded border-2 border-foreground bg-surface shadow-brutal divide-y-2 divide-foreground">
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
                <h2 className="mb-3 text-lg font-black uppercase text-foreground">
                  {section.title}
                </h2>
                <p className="text-muted-foreground leading-[1.8] text-sm font-mono">
                  {section.body}
                </p>
              </section>
            );
          })}
        </div>

        <div className="mt-12 pt-8 border-t-2 border-foreground text-center">
          <Link
            href="/"
            className="inline-flex min-h-10 items-center gap-2 rounded border-2 border-foreground bg-surface px-3 py-1.5 text-xs font-black uppercase text-foreground shadow-brutal-sm transition-all duration-[100ms] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-brutal active:translate-x-0 active:translate-y-0 active:shadow-none"
          >
            <PixelIcon name="play" className="size-3 rotate-180" />
            Return to Motionly
          </Link>
        </div>
      </div>
    </main>
  );
}
