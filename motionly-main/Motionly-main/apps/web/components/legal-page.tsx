import Link from "next/link";

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
    <main className="mx-auto min-h-screen w-full max-w-3xl px-5 py-12">
      <Link href="/" className="text-sm font-semibold text-muted-foreground hover:text-foreground">
        Motionly
      </Link>
      <div className="mt-10 border-b border-border pb-6">
        <p className="text-sm font-medium text-muted-foreground">Updated {updated}</p>
        <h1 className="mt-2 text-3xl font-bold">{title}</h1>
      </div>
      <div className="divide-y divide-border">
        {sections.map((section) => (
          <section key={section.title} className="py-6">
            <h2 className="text-lg font-bold">{section.title}</h2>
            <p className="mt-2 leading-7 text-muted-foreground">{section.body}</p>
          </section>
        ))}
      </div>
    </main>
  );
}
