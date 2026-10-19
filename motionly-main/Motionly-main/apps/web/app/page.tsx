import Image from "next/image";
import Link from "next/link";
import { ButtonLink } from "@/components/button";
import { Badge, Card } from "@/components/brutal-ui";
import { PixelIcon, type PixelIconName } from "@/components/pixel-icons";
import { ThemeMenu } from "@/components/theme-menu";
import { PageTitle } from "@/components/page-title";

const navItems = [
  { href: "/pricing", label: "Pricing" },
  { href: "#features", label: "Features" },
];

const pipeline = [
  {
    step: "01",
    icon: "upload",
    title: "Upload",
    body: "Drop an MP4, MOV, or WebM. Motionly preflights the source before AI work starts.",
  },
  {
    step: "02",
    icon: "ai-cut",
    title: "AI cut",
    body: "Generate a full timeline with cut points, captions, B-roll slots, and music regions.",
  },
  {
    step: "03",
    icon: "export",
    title: "Export",
    body: "Review the edit console, tune timing, and render a vertical MP4 for short-form channels.",
  },
] satisfies Array<{
  step: string;
  icon: PixelIconName;
  title: string;
  body: string;
}>;

const features = [
  [
    "upload",
    "Source preflight",
    "Audio, duration, format, and warning states stay visible before processing.",
  ],
  [
    "ai-cut",
    "AI edit plan",
    "Generated cuts become editable timeline clips with undo-friendly saves.",
  ],
  [
    "captions",
    "Caption timing",
    "Word-level captions stay inspectable and adjustable in the editor.",
  ],
  [
    "broll",
    "B-roll lanes",
    "B-roll slots land in their own track with visible warnings and disabled states.",
  ],
  [
    "music",
    "Music regions",
    "Beat and mood choices stay editable as timeline regions.",
  ],
  [
    "export",
    "Render queue",
    "Exports surface progress, failure states, and download actions clearly.",
  ],
] satisfies Array<[PixelIconName, string, string]>;

export default function HomePage() {
  return (
    <main className="min-h-screen overflow-hidden">
      <PageTitle />
      <nav className="sticky top-0 z-40 border-b-4 border-foreground bg-surface px-4 sm:px-6 lg:px-8">
        <div className="container-dashboard flex h-16 items-center justify-between gap-4">
          <Link href="/" className="flex min-w-0 items-center gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded border-2 border-foreground bg-primary shadow-brutal-sm">
              <Image
                src="/icon.svg"
                alt="Motionly"
                width={32}
                height={32}
                className="size-8"
                priority
              />
            </span>
            <span className="truncate text-lg font-black uppercase text-foreground">
              Motionly
            </span>
          </Link>

          <div className="hidden items-center gap-6 sm:flex">
            {navItems.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="inline-flex min-h-11 items-center text-sm font-black uppercase text-muted-foreground hover:text-foreground"
              >
                {item.label}
              </Link>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <ThemeMenu
              items={[
                ...navItems,
                { href: "/login", label: "Log in" },
                { href: "/signup", label: "Start free" },
              ]}
            />
            <Link
              href="/login"
              className="hidden min-h-11 items-center px-3 text-sm font-black uppercase text-muted-foreground hover:text-foreground sm:inline-flex"
            >
              Log in
            </Link>
            <ButtonLink href="/signup" size="sm">
              Start free
              <PixelIcon name="play" className="size-3.5" />
            </ButtonLink>
          </div>
        </div>
      </nav>

      <section className="border-b-4 border-foreground bg-background px-4 py-14 sm:px-6 lg:px-8 lg:py-20">
        <div className="container-dashboard grid items-center gap-10 lg:grid-cols-[minmax(0,0.88fr)_minmax(420px,1.12fr)]">
          <div className="max-w-2xl">
            <Badge variant="primary">Pixel edit console</Badge>
            <h1 className="mt-5 text-5xl font-black uppercase leading-[0.95] text-foreground sm:text-6xl lg:text-7xl">
              Motionly turns rough clips into editable reels.
            </h1>
            <p className="mt-6 max-w-xl text-lg font-bold leading-relaxed text-muted-foreground">
              Upload a talking-head video, review the AI edit proposal, then
              tune captions, B-roll, music, and export from one hard-edged
              creator workspace.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <ButtonLink href="/signup" size="lg">
                Build a reel
                <PixelIcon name="play" className="size-4" />
              </ButtonLink>
              <ButtonLink href="/pricing" variant="secondary" size="lg">
                View plans
              </ButtonLink>
            </div>
            <div className="mt-6 grid max-w-xl gap-2 sm:grid-cols-3">
              {["AI timeline", "Caption lanes", "Render status"].map((item) => (
                <div
                  key={item}
                  className="rounded border-2 border-foreground bg-surface px-3 py-2 font-mono text-xs font-black uppercase shadow-brutal-sm"
                >
                  {item}
                </div>
              ))}
            </div>
          </div>

          <PixelEditorScene />
        </div>
      </section>

      <section
        id="features"
        className="border-b-4 border-foreground bg-surface px-4 py-16 sm:px-6 lg:px-8"
      >
        <div className="container-dashboard">
          <div className="max-w-2xl">
            <Badge variant="accent">Workflow</Badge>
            <h2 className="mt-4 text-4xl font-black uppercase text-foreground sm:text-5xl">
              Three stops from upload to post-ready.
            </h2>
          </div>
          <div className="mt-10 grid gap-5 md:grid-cols-3">
            {pipeline.map((item) => (
              <Card key={item.step} className="p-5">
                <div className="flex items-start justify-between gap-4">
                  <span className="flex size-12 items-center justify-center rounded border-2 border-foreground bg-primary text-primary-foreground shadow-brutal-sm">
                    <PixelIcon name={item.icon} className="size-6" />
                  </span>
                  <span className="font-mono text-4xl font-black text-primary">
                    {item.step}
                  </span>
                </div>
                <h3 className="mt-5 text-xl font-black uppercase">
                  {item.title}
                </h3>
                <p className="mt-2 text-sm font-bold leading-relaxed text-muted-foreground">
                  {item.body}
                </p>
              </Card>
            ))}
          </div>
        </div>
      </section>

      <section className="border-b-4 border-foreground bg-background px-4 py-16 sm:px-6 lg:px-8">
        <div className="container-dashboard">
          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
            <div>
              <Badge variant="primary">Editor lanes</Badge>
              <h2 className="mt-4 text-4xl font-black uppercase text-foreground sm:text-5xl">
                A product UI built for timing, not guessing.
              </h2>
            </div>
            <ButtonLink href="/signup" variant="secondary">
              Open workspace
            </ButtonLink>
          </div>
          <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {features.map(([icon, title, body]) => (
              <Card key={title} className="p-5">
                <span className="mb-4 flex size-11 items-center justify-center rounded border-2 border-foreground bg-accent text-accent-foreground shadow-brutal-sm">
                  <PixelIcon name={icon} className="size-5" />
                </span>
                <h3 className="text-base font-black uppercase">{title}</h3>
                <p className="mt-2 text-sm font-bold leading-relaxed text-muted-foreground">
                  {body}
                </p>
              </Card>
            ))}
          </div>
        </div>
      </section>

      <section className="bg-surface px-4 py-14 sm:px-6 lg:px-8">
        <div className="container-content border-4 border-foreground bg-primary p-6 text-center text-primary-foreground shadow-brutal-lg sm:p-10">
          <h2 className="text-4xl font-black uppercase sm:text-5xl">
            Start with one clip.
          </h2>
          <p className="mx-auto mt-4 max-w-lg text-base font-black">
            No credit card required. Create a project, upload source footage,
            and review the generated edit before exporting.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <ButtonLink href="/signup" variant="secondary" size="lg">
              Start free
            </ButtonLink>
            <ButtonLink href="/pricing" variant="ghost" size="lg">
              Compare plans
            </ButtonLink>
          </div>
        </div>
      </section>

      <footer className="border-t-4 border-foreground bg-background px-4 py-10 sm:px-6 lg:px-8">
        <div className="container-dashboard flex flex-wrap items-center justify-between gap-5">
          <div className="flex items-center gap-3">
            <span className="flex size-9 items-center justify-center rounded border-2 border-foreground bg-primary shadow-brutal-sm">
              <Image
                src="/icon.svg"
                alt=""
                width={28}
                height={28}
                className="size-7"
              />
            </span>
            <span className="font-black uppercase">Motionly</span>
          </div>
          <nav
            className="flex flex-wrap gap-x-5 gap-y-2"
            aria-label="Footer links"
          >
            {[
              { href: "/pricing", label: "Pricing" },
              { href: "/privacy", label: "Privacy" },
              { href: "/terms", label: "Terms" },
              { href: "/refund", label: "Refund" },
              { href: "/copyright", label: "Copyright" },
              { href: "/feedback", label: "Feedback" },
            ].map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="inline-flex min-h-10 items-center text-xs font-black uppercase text-muted-foreground hover:text-foreground"
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <p className="text-xs font-bold text-muted-foreground">
            (c) {new Date().getFullYear()} Motionly
          </p>
        </div>
      </footer>
    </main>
  );
}

function PixelEditorScene() {
  const tracks = [
    ["Video", "bg-primary", 14, 36, 56],
    ["Captions", "bg-accent", 22, 30, 42],
    ["B-roll", "bg-info", 9, 24, 36],
    ["Audio", "bg-success", 18, 64, 12],
  ] as const;

  return (
    <div className="relative min-h-[430px]">
      <div className="absolute inset-0 border-4 border-foreground bg-surface shadow-brutal-lg" />
      <div className="relative grid min-h-[430px] grid-rows-[52px_minmax(0,1fr)_140px]">
        <div className="flex items-center gap-3 border-b-4 border-foreground bg-background px-4">
          <div className="flex gap-1.5">
            {["bg-danger", "bg-warning", "bg-primary"].map((tone) => (
              <span
                key={tone}
                className={`size-3 border-2 border-foreground ${tone}`}
              />
            ))}
          </div>
          <div className="ml-auto rounded border-2 border-foreground bg-surface px-3 py-1 font-mono text-[10px] font-black uppercase">
            Project 001 / 00:18.4
          </div>
        </div>

        <div className="grid min-h-0 grid-cols-[96px_minmax(0,1fr)_118px]">
          <aside className="border-r-4 border-foreground bg-surface-elevated p-3">
            {[
              ["upload", "Media"],
              ["ai-cut", "AI Cut"],
              ["captions", "Text"],
              ["broll", "B-roll"],
              ["music", "Music"],
            ].map(([icon, label]) => (
              <div
                key={label}
                className="mb-2 flex min-h-10 items-center gap-2 rounded border-2 border-foreground bg-surface px-2 text-[10px] font-black uppercase shadow-brutal-sm"
              >
                <PixelIcon name={icon as PixelIconName} className="size-4" />
                {label}
              </div>
            ))}
          </aside>

          <section className="pixel-grid-fine flex items-center justify-center bg-background p-4">
            <div className="relative aspect-[9/16] h-[270px] border-4 border-foreground bg-ink shadow-brutal">
              <div className="absolute inset-3 grid grid-rows-[1fr_auto] border-2 border-primary/60 bg-surface">
                <div className="pixel-grid flex items-center justify-center">
                  <span className="flex size-14 items-center justify-center rounded border-2 border-foreground bg-primary text-primary-foreground shadow-brutal-sm">
                    <PixelIcon name="play" className="size-7" />
                  </span>
                </div>
                <div className="border-t-2 border-foreground bg-primary px-2 py-2 text-center text-[10px] font-black uppercase text-primary-foreground">
                  Captions locked to cut
                </div>
              </div>
            </div>
          </section>

          <aside className="border-l-4 border-foreground bg-surface-elevated p-3">
            {["Snap", "Cuts", "Render"].map((label, index) => (
              <div
                key={label}
                className="mb-3 border-2 border-foreground bg-surface p-2 shadow-brutal-sm"
              >
                <div className="flex justify-between font-mono text-[10px] font-black uppercase">
                  <span>{label}</span>
                  <span>{index === 0 ? "On" : `${72 - index * 18}%`}</span>
                </div>
                <div className="mt-2 h-3 border-2 border-foreground bg-muted">
                  <div
                    className="h-full bg-primary"
                    style={{ width: `${84 - index * 21}%` }}
                  />
                </div>
              </div>
            ))}
          </aside>
        </div>

        <div className="border-t-4 border-foreground bg-background">
          <div className="flex h-8 items-center border-b-2 border-foreground px-3 font-mono text-[10px] font-black uppercase text-muted-foreground">
            <span>Timeline</span>
            <span className="ml-auto text-primary">Zoom 1.4x</span>
          </div>
          <div className="relative p-3">
            <div className="absolute bottom-0 top-0 left-[38%] w-1 bg-danger" />
            {tracks.map(([label, color, left, width, top]) => (
              <div
                key={label}
                className="relative mb-2 h-7 border-2 border-foreground bg-surface"
              >
                <span className="absolute left-2 top-1/2 z-10 -translate-y-1/2 bg-background px-1 font-mono text-[9px] font-black uppercase">
                  {label}
                </span>
                <span
                  className={`absolute top-1 h-4 border-2 border-foreground ${color}`}
                  style={{
                    left: `${left}%`,
                    width: `${width}%`,
                    opacity: top / 64,
                  }}
                />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
