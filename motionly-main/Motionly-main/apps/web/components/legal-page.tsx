import Link from "next/link";
import { ChevronLeft } from "lucide-react";

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
      <div className="container-content py-12 lg:py-16">
        {/* Back link */}
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 rounded border-2 border-foreground bg-surface px-3 py-1.5 font-bold text-xs text-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] dark:hover:shadow-[2px_2px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] mb-10"
        >
          <ChevronLeft
            className="size-4 text-foreground"
            strokeWidth={2}
            aria-hidden="true"
          />
          Back to Motionly
        </Link>

        {/* Header */}
        <div className="mb-10">
          <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground font-mono mb-3">
            Last updated {updated}
          </p>
          <h1 className="text-4xl font-black uppercase tracking-tight sm:text-5xl text-foreground">{title}</h1>
        </div>

        {/* Content */}
        <div className="card rounded border-2 border-foreground bg-surface shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] overflow-hidden divide-y-2 divide-foreground">
          {sections.map((section) => {
            const id = section.title
              .toLowerCase()
              .replace(/[^a-z0-9]+/g, "-")
              .replace(/^-|-$/g, "");
            return (
              <section key={section.title} id={id} className="px-6 py-7 sm:px-8">
                <h2 className="text-lg font-black uppercase tracking-tight text-foreground mb-3">{section.title}</h2>
                <p className="text-muted-foreground leading-[1.8] text-sm font-mono">{section.body}</p>
              </section>
            );
          })}
        </div>

        {/* Footer */}
        <div className="mt-12 pt-8 border-t-2 border-foreground text-center">
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 rounded border-2 border-foreground bg-surface px-3 py-1.5 font-bold text-xs text-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] dark:hover:shadow-[2px_2px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms]"
          >
            <ChevronLeft className="size-4 text-foreground" strokeWidth={2} aria-hidden="true" />
            Return to Motionly
          </Link>
        </div>
      </div>
    </main>
  );
}
