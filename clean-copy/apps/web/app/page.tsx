import {
  ArrowRight,
  CheckCircle2,
  Download,
  Play,
  Sparkles,
  Type,
  Layers,
  Music2,
  Zap,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { ButtonLink } from "@/components/button";
import { ThemeMenu } from "@/components/theme-menu";

export default function HomePage() {
  return (
    <main className="min-h-screen overflow-hidden">
      {/* ── Navbar ─────────────────────────────────────────────────── */}
      <nav className="bg-surface sticky top-0 z-40 flex h-16 items-center justify-between px-4 sm:px-6 lg:px-8 border-b-4 border-foreground">
        <Link href="/" className="flex items-center gap-3 group">
          <div className="flex size-8 items-center justify-center rounded border-2 border-foreground bg-primary shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
            <Image src="/icon.svg" alt="Motionly" width={20} height={20} className="size-5 invert dark:invert-0" priority />
          </div>
          <span className="text-base font-black uppercase tracking-tight text-foreground">Motionly</span>
        </Link>

        <div className="hidden sm:flex items-center gap-6">
          <Link href="/pricing" className="text-sm font-bold text-muted-foreground hover:text-foreground transition-colors uppercase tracking-wider">
            Pricing
          </Link>
          <Link href="#features" className="text-sm font-bold text-muted-foreground hover:text-foreground transition-colors uppercase tracking-wider">
            Features
          </Link>
        </div>

        <div className="flex items-center gap-2">
          <ThemeMenu
            items={[
              { href: "/pricing", label: "Pricing" },
              { href: "#features", label: "Features" },
              { href: "/login", label: "Log in" },
              { href: "/signup", label: "Start free" },
            ]}
          />
          <Link
            href="/login"
            className="hidden sm:inline-flex items-center text-sm font-bold text-muted-foreground hover:text-foreground transition-colors px-3 py-2 uppercase tracking-wider"
          >
            Log in
          </Link>
          <ButtonLink href="/signup" className="h-9 px-4 text-sm">
            Start free
            <ArrowRight className="ml-1.5 size-3.5" aria-hidden="true" />
          </ButtonLink>
        </div>
      </nav>

      {/* ── Hero ───────────────────────────────────────────────────── */}
      <section className="relative px-4 py-20 sm:px-6 lg:px-8 lg:py-28 bg-background">
        <div className="relative container-dashboard grid items-center gap-12 lg:grid-cols-2 lg:gap-16">
          {/* Left: Copy */}
          <div className="max-w-2xl">
            {/* Eyebrow badge */}
            <div className="inline-flex items-center gap-2 rounded border-2 border-foreground bg-primary px-3.5 py-1.5 text-xs font-black text-primary-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
              <span className="size-1.5 rounded-full bg-foreground" aria-hidden="true" />
              PREMIUM AI EDITING COCKPIT
            </div>

            <h1 className="mt-6 text-5xl font-black uppercase tracking-tighter leading-[0.95] text-foreground sm:text-6xl lg:text-7xl">
              Make polished <span className="text-primary bg-foreground text-background dark:text-foreground dark:bg-primary px-2">reels</span> from rough footage.
            </h1>

            <p className="mt-6 max-w-xl text-lg font-bold leading-relaxed text-muted-foreground">
              Upload a talking-head clip and get a complete AI-populated timeline
              with cuts, captions, B-roll, music, and visual polish — then export
              a vertical MP4 for Instagram, TikTok, or Shorts.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <ButtonLink href="/signup" className="h-12 px-8 text-base font-bold">
                Start free
                <ArrowRight className="ml-2 size-4" aria-hidden="true" />
              </ButtonLink>
              <Link
                href="/pricing"
                className="btn btn-secondary h-12 px-8 text-base font-bold"
              >
                View pricing
              </Link>
            </div>

            {/* Feature chips */}
            <div className="mt-8 flex flex-wrap gap-2.5">
              {[
                { icon: CheckCircle2, label: "AI-populated timelines" },
                { icon: CheckCircle2, label: "Caption timing" },
                { icon: CheckCircle2, label: "1-click render pipeline" },
              ].map(({ icon: Icon, label }) => (
                <div
                  key={label}
                  className="flex items-center gap-1.5 rounded border-2 border-foreground bg-surface px-3 py-1.5 text-xs font-bold text-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
                >
                  <Icon className="size-3.5 shrink-0 text-primary" strokeWidth={2.5} aria-hidden="true" />
                  {label}
                </div>
              ))}
            </div>
          </div>

          {/* Right: Animated Editor Mockup */}
          <div className="relative mx-auto w-full max-w-[500px]">
            <div className="relative rounded-xl border-4 border-foreground bg-surface shadow-[6px_6px_0px_#000] dark:shadow-[6px_6px_0px_#fff] overflow-hidden">
              {/* Toolbar */}
              <div className="flex h-10 items-center gap-2 border-b-2 border-foreground px-4 bg-surface-elevated">
                <div className="flex gap-1.5">
                  {["bg-red-400", "bg-amber-400", "bg-green-400"].map((c, i) => (
                    <div key={i} className={`size-2.5 rounded border border-foreground ${c}`} />
                  ))}
                </div>
                <div className="mx-auto flex h-6 w-40 items-center rounded border-2 border-foreground bg-muted px-3">
                  <span className="text-[10px] font-bold text-foreground font-mono">Motionly Editor</span>
                </div>
              </div>

              {/* 3-panel editor */}
              <div className="grid min-h-[360px] grid-cols-[80px_1fr_120px]">
                {/* Left panel */}
                <aside className="border-r-2 border-foreground bg-muted/40 p-2">
                  <p className="mb-2 text-[8px] font-black uppercase tracking-widest text-foreground font-mono">Elements</p>
                  {["AI Edit", "Captions", "B-roll", "Music", "Look"].map((item, i) => (
                    <div
                      key={item}
                      className={`mb-1 rounded border border-foreground px-2 py-1 text-[9px] font-bold ${i === 0 ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted bg-surface shadow-[1px_1px_0px_#000]"}`}
                    >
                      {item}
                    </div>
                  ))}
                </aside>

                {/* Center: phone preview */}
                <section className="flex flex-col items-center justify-center bg-muted/20 p-4">
                  <div className="relative aspect-[9/16] w-[100px] overflow-hidden rounded border-2 border-foreground bg-background shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff]">
                    {/* Caption overlay */}
                    <div className="absolute bottom-4 left-1 right-1 rounded border border-foreground bg-primary px-1.5 py-1 text-center shadow-[1px_1px_0px_#000]">
                      <p className="text-[6px] font-black leading-tight text-primary-foreground uppercase">
                        Captioned.{" "}
                        <span className="bg-foreground text-background px-0.5">Cut.</span>{" "}
                        Ready.
                      </p>
                    </div>
                    {/* Fake content bars */}
                    <div className="absolute inset-0 flex flex-col justify-between p-2 pb-10">
                      {[70, 85, 60, 75].map((w, i) => (
                        <div
                          key={i}
                          className="h-1 rounded bg-foreground/20"
                          style={{ width: `${w}%`, animationDelay: `${i * 0.3}s` }}
                        />
                      ))}
                    </div>
                    {/* Play button */}
                    <div className="absolute inset-0 flex items-center justify-center">
                      <div className="flex size-7 items-center justify-center rounded border-2 border-foreground bg-primary shadow-[1px_1px_0px_#000]">
                        <Play className="size-2.5 fill-primary-foreground text-primary-foreground" aria-hidden="true" />
                      </div>
                    </div>
                  </div>
                  {/* Timeline strip */}
                  <div className="mt-3 grid w-full grid-cols-5 gap-0.5">
                    {Array.from({ length: 10 }).map((_, i) => (
                      <div
                        key={i}
                        className={`h-2 border border-foreground rounded-sm ${i % 3 === 0 ? "bg-primary" : i % 3 === 1 ? "bg-accent" : "bg-muted"}`}
                      />
                    ))}
                  </div>
                </section>

                {/* Right panel */}
                <aside className="border-l-2 border-foreground bg-muted/40 p-2">
                  <p className="mb-2 text-[8px] font-black uppercase tracking-widest text-foreground font-mono">AI Tools</p>
                  {["Auto-caption", "B-roll fill", "Music match"].map((item) => (
                    <div key={item} className="mb-2 rounded border-2 border-foreground bg-surface p-2 shadow-[1px_1px_0px_#000]">
                      <p className="text-[8px] font-bold text-foreground leading-tight">{item}</p>
                      <div className="mt-1.5 h-1.5 w-full rounded border border-foreground bg-muted overflow-hidden">
                        <div className="h-full bg-primary" style={{ width: "66%" }} />
                      </div>
                    </div>
                  ))}
                </aside>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── How It Works ───────────────────────────────────────────── */}
      <section className="px-4 py-24 sm:px-6 lg:px-8 border-t-4 border-foreground bg-background" id="features">
        <div className="container-dashboard">
          <div className="text-center">
            <span className="badge badge-primary mb-4 inline-flex">How it works</span>
            <h2 className="text-4xl font-black tracking-tight sm:text-5xl uppercase">
              From raw clip to reel <span className="text-primary">in minutes</span>
            </h2>
            <p className="mt-4 mx-auto max-w-xl text-muted-foreground font-bold">
              Three steps from upload to export. AI does the heavy lifting.
            </p>
          </div>

          <div className="mt-16 grid gap-8 sm:grid-cols-3">
            {[
              {
                step: "01",
                icon: Download,
                title: "Upload your clip",
                desc: "Drop an MP4, MOV, or WebM. Motionly validates your audio and detects format, resolution, and duration.",
              },
              {
                step: "02",
                icon: Sparkles,
                title: "AI drafts your edit",
                desc: "Pick a storytelling style. AI generates captions, indexes B-roll, maps music beats, and populates a full timeline.",
              },
              {
                step: "03",
                icon: Zap,
                title: "Fine-tune & export",
                desc: "Tweak captions, drag overlays, swap music, adjust the look — then render a 1080p vertical MP4 ready for posting.",
              },
            ].map(({ step, icon: Icon, title, desc }) => (
              <div key={step} className="card rounded-lg p-6 group shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff]">
                <div className="mb-5 flex items-start justify-between">
                  <div className="flex size-11 items-center justify-center rounded border-2 border-foreground bg-primary shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                    <Icon className="size-5 text-primary-foreground" strokeWidth={2} aria-hidden="true" />
                  </div>
                  <span className="text-4xl font-black text-foreground/20 font-mono">{step}</span>
                </div>
                <h3 className="text-lg font-black uppercase tracking-tight">{title}</h3>
                <p className="mt-2 text-xs leading-relaxed text-muted-foreground font-mono">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Feature Grid ───────────────────────────────────────────── */}
      <section className="px-4 py-20 sm:px-6 lg:px-8 border-t-4 border-foreground bg-surface-elevated">
        <div className="container-dashboard">
          <div className="text-center mb-12">
            <span className="badge badge-accent mb-4 inline-flex font-mono">Features</span>
            <h2 className="text-4xl font-black tracking-tight sm:text-5xl uppercase">
              Every tool your reel needs
            </h2>
          </div>

          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {[
              {
                icon: Type,
                title: "Word-perfect captions",
                desc: "Word-level timing with highlight detection and 10+ style presets.",
                color: "bg-primary",
              },
              {
                icon: Layers,
                title: "Smart B-roll",
                desc: "AI-indexes relevant footage and drops it at exactly the right moments.",
                color: "bg-accent",
              },
              {
                icon: Music2,
                title: "Music matching",
                desc: "Beat-synced track selection that matches the mood of your script.",
                color: "bg-red-400",
              },
              {
                icon: Sparkles,
                title: "Visual grading",
                desc: "15+ cinematic LUT presets, grain, vignette, and gradient ramps.",
                color: "bg-amber-400",
              },
            ].map(({ icon: Icon, title, desc, color }) => (
              <div key={title} className="card rounded-lg p-5 group shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff]">
                <div className={`mb-4 flex size-10 items-center justify-center rounded border-2 border-foreground ${color} text-black shadow-[2px_2px_0px_#000]`}>
                  <Icon className="size-5" strokeWidth={2} aria-hidden="true" />
                </div>
                <h3 className="font-black uppercase tracking-tight text-sm">{title}</h3>
                <p className="mt-2 text-xs font-mono text-muted-foreground leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA Banner ─────────────────────────────────────────────── */}
      <section className="px-4 py-24 sm:px-6 lg:px-8 border-t-4 border-foreground bg-background">
        <div className="container-content">
          <div className="relative overflow-hidden rounded-xl border-4 border-foreground bg-surface p-12 text-center shadow-[6px_6px_0px_#000] dark:shadow-[6px_6px_0px_#fff]">
            <div className="relative">
              <h2 className="text-4xl font-black uppercase tracking-tight sm:text-5xl">
                Start building reels today
              </h2>
              <p className="mx-auto mt-4 max-w-md text-muted-foreground font-bold">
                No credit card required. Upload your first clip and see AI edit it in minutes.
              </p>
              <div className="mt-8 flex flex-wrap items-center justify-center gap-4">
                <ButtonLink href="/signup" className="h-12 px-8 text-base font-bold">
                  Get started free
                  <ArrowRight className="ml-2 size-4" aria-hidden="true" />
                </ButtonLink>
                <ButtonLink href="/pricing" variant="secondary" className="h-12 px-8 text-base font-bold">
                  See pricing
                </ButtonLink>
              </div>
              <p className="mt-4 text-xs font-mono text-muted-foreground">
                Free plan includes 10 renders/month · No watermark on Free
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ── Footer ─────────────────────────────────────────────────── */}
      <footer className="border-t-4 border-foreground bg-surface px-4 py-10 sm:px-6 lg:px-8">
        <div className="container-dashboard flex flex-wrap items-center justify-between gap-6">
          <div className="flex items-center gap-3">
            <div className="flex size-7 items-center justify-center rounded border-2 border-foreground bg-primary shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
              <Image src="/icon.svg" alt="" width={16} height={16} className="size-4 invert dark:invert-0" />
            </div>
            <span className="font-black uppercase tracking-tight text-sm text-foreground">Motionly</span>
            <span className="text-xs font-mono text-muted-foreground hidden sm:inline">
              · AI-powered short-form video editing
            </span>
          </div>
          <nav className="flex flex-wrap gap-x-6 gap-y-2" aria-label="Footer links">
            {[
              { href: "/pricing", label: "Pricing" },
              { href: "/privacy", label: "Privacy" },
              { href: "/terms", label: "Terms" },
              { href: "/refund", label: "Refund" },
              { href: "/copyright", label: "Copyright" },
            ].map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                className="text-xs font-bold text-muted-foreground hover:text-foreground transition-colors uppercase tracking-wider"
              >
                {label}
              </Link>
            ))}
          </nav>
          <p className="text-xs text-muted-foreground">
            © {new Date().getFullYear()} Motionly
          </p>
        </div>
      </footer>
    </main>
  );
}
