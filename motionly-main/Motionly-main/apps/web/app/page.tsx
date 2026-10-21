import Image from "next/image";
import Link from "next/link";
import { ButtonLink } from "@/components/button";
import { Badge } from "@/components/brutal-ui";
import {
  RegisterCommands,
  type PaletteCommand,
} from "@/components/command-palette";
import { HeroConsole } from "@/components/landing/hero-console";
import { Reveal } from "@/components/landing/reveal";
import { PixelIcon, type PixelIconName } from "@/components/pixel-icons";
import { ThemeMenu } from "@/components/theme-menu";
import { PageTitle } from "@/components/page-title";

const landingCommands: PaletteCommand[] = [
  {
    id: "landing-pricing",
    label: "Pricing",
    group: "Navigate",
    href: "/pricing",
    icon: "billing",
  },
  {
    id: "landing-features",
    label: "Features",
    group: "Navigate",
    href: "/#features",
    icon: "dashboard",
  },
  {
    id: "landing-login",
    label: "Log in",
    group: "Navigate",
    href: "/login",
    icon: "settings",
  },
  {
    id: "landing-signup",
    label: "Start free",
    group: "Create",
    href: "/signup",
    icon: "new",
  },
];

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
    <main className="min-h-screen overflow-hidden bg-background">
      <PageTitle />
      <RegisterCommands source="landing" commands={landingCommands} />
      <div className="sticky top-4 z-40 px-4 sm:px-6 lg:px-8">
        <nav className="container-dashboard flex h-16 items-center justify-between gap-4 rounded-full bg-surface px-4 shadow-neu-raised-lg sm:px-6">
          <Link
            href="/"
            className="flex min-w-0 items-center gap-3 rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-primary shadow-neu-glow">
              <Image
                src="/icon.svg"
                alt="Motionly"
                width={32}
                height={32}
                className="size-7"
                priority
              />
            </span>
            <span className="truncate text-lg font-extrabold tracking-tight text-foreground">
              Motionly
            </span>
          </Link>

          <div className="hidden items-center gap-1 sm:flex">
            {navItems.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="inline-flex min-h-11 items-center rounded-full px-4 text-sm font-semibold text-muted-foreground transition-colors hover:text-foreground"
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
            <a
              href="/login"
              className="hidden min-h-11 items-center rounded-full px-4 text-sm font-semibold text-muted-foreground transition-colors hover:text-foreground sm:inline-flex"
            >
              Log in
            </a>
            <a
              href="/signup"
              className="inline-flex min-h-10 items-center justify-center gap-2 rounded-full bg-primary px-4 text-xs font-semibold text-primary-foreground shadow-neu-glow transition-shadow hover:shadow-neu-raised-lg active:shadow-neu-pressed"
            >
              Start free
              <PixelIcon name="play" className="size-3.5" />
            </a>
          </div>
        </nav>
      </div>

      <section className="px-4 py-14 sm:px-6 lg:px-8 lg:py-20">
        <div className="container-dashboard grid items-center gap-10 lg:grid-cols-[minmax(0,0.88fr)_minmax(420px,1.12fr)]">
          <Reveal className="max-w-2xl">
            <Badge variant="primary">Soft-touch edit console</Badge>
            <h1 className="mt-5 text-5xl font-extrabold leading-[1.02] tracking-tight text-foreground sm:text-6xl lg:text-7xl">
              Motionly turns rough clips into editable reels.
            </h1>
            <p className="mt-6 max-w-xl text-lg font-medium leading-relaxed text-muted-foreground">
              Upload a talking-head video, review the AI edit proposal, then
              tune captions, B-roll, music, and export from one tactile creator
              workspace.
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
            <div className="mt-8 flex flex-wrap gap-2">
              {["AI timeline", "Caption lanes", "Render status"].map((item) => (
                <span
                  key={item}
                  className="inline-flex items-center gap-1.5 rounded-full bg-surface px-3 py-1.5 text-xs font-semibold text-foreground shadow-neu-raised-sm"
                >
                  {item}
                </span>
              ))}
            </div>
          </Reveal>

          <Reveal delay={0.08}>
            <HeroConsole />
          </Reveal>
        </div>
      </section>

      <section id="features" className="px-4 py-16 sm:px-6 lg:px-8">
        <div className="container-dashboard">
          <Reveal className="max-w-2xl">
            <Badge variant="accent">Workflow</Badge>
            <h2 className="mt-4 text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
              Three stops from upload to post-ready.
            </h2>
          </Reveal>
          <div className="mt-10 grid gap-6 md:grid-cols-3">
            {pipeline.map((item, index) => (
              <Reveal key={item.step} delay={index * 0.06}>
                <article className="group h-full rounded-neu-lg bg-surface p-6 shadow-neu-raised transition-shadow duration-200 hover:shadow-neu-raised-lg">
                  <div className="flex items-start justify-between gap-4">
                    <span className="flex size-12 items-center justify-center rounded-neu bg-primary text-primary-foreground shadow-neu-glow">
                      <PixelIcon name={item.icon} className="size-6" />
                    </span>
                    <span className="inline-flex items-center rounded-full bg-muted px-3 py-1 text-sm font-bold tabular-nums text-primary shadow-neu-inset-sm">
                      {item.step}
                    </span>
                  </div>
                  <h3 className="mt-5 text-xl font-bold text-foreground">
                    {item.title}
                  </h3>
                  <p className="mt-2 text-sm font-medium leading-relaxed text-muted-foreground">
                    {item.body}
                  </p>
                </article>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <section className="px-4 py-16 sm:px-6 lg:px-8">
        <div className="container-dashboard">
          <Reveal>
            <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
              <div>
                <Badge variant="primary">Editor lanes</Badge>
                <h2 className="mt-4 text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
                  A product UI built for timing, not guessing.
                </h2>
              </div>
              <ButtonLink href="/signup" variant="secondary">
                Open workspace
              </ButtonLink>
            </div>
          </Reveal>
          <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {features.map(([icon, title, body], index) => (
              <Reveal key={title} delay={(index % 3) * 0.06}>
                <article className="group h-full rounded-neu-lg bg-surface p-6 shadow-neu-raised transition-shadow duration-200 hover:shadow-neu-raised-lg">
                  <span className="mb-4 flex size-11 items-center justify-center rounded-neu bg-accent text-accent-foreground shadow-neu-raised-sm">
                    <PixelIcon name={icon} className="size-5" />
                  </span>
                  <h3 className="text-base font-bold text-foreground">
                    {title}
                  </h3>
                  <p className="mt-2 text-sm font-medium leading-relaxed text-muted-foreground">
                    {body}
                  </p>
                </article>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <section className="px-4 py-14 sm:px-6 lg:px-8">
        <Reveal className="container-content">
          <div className="rounded-neu-2xl bg-surface p-8 text-center shadow-neu-raised-lg sm:p-12">
            <span className="mx-auto flex size-14 items-center justify-center rounded-neu bg-primary text-primary-foreground shadow-neu-glow">
              <PixelIcon name="new" className="size-7" />
            </span>
            <h2 className="mt-6 text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
              Start with one clip.
            </h2>
            <p className="mx-auto mt-4 max-w-lg text-base font-medium leading-relaxed text-muted-foreground">
              No credit card required. Create a project, upload source footage,
              and review the generated edit before exporting.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <ButtonLink href="/signup" size="lg">
                Start free
              </ButtonLink>
              <ButtonLink href="/pricing" variant="secondary" size="lg">
                Compare plans
              </ButtonLink>
            </div>
          </div>
        </Reveal>
      </section>

      <footer className="px-4 py-10 sm:px-6 lg:px-8">
        <div className="container-dashboard flex flex-wrap items-center justify-between gap-5 rounded-neu-xl bg-surface px-6 py-6 shadow-neu-raised">
          <div className="flex items-center gap-3">
            <span className="flex size-9 items-center justify-center rounded-full bg-primary shadow-neu-raised-sm">
              <Image
                src="/icon.svg"
                alt=""
                width={28}
                height={28}
                className="size-6"
              />
            </span>
            <span className="font-bold text-foreground">Motionly</span>
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
                className="inline-flex min-h-10 items-center rounded-full px-1 text-xs font-semibold text-muted-foreground transition-colors hover:text-foreground"
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <p className="text-xs font-medium text-muted-foreground">
            (c) {new Date().getFullYear()} Motionly
          </p>
        </div>
      </footer>
    </main>
  );
}
