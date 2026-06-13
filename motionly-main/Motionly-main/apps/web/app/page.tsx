import { ArrowRight, CheckCircle2, Clapperboard, Play, Sparkles, Wand2 } from "lucide-react";
import Image from "next/image";
import { ButtonLink } from "@/components/button";
import { ThemeMenu } from "@/components/theme-menu";

export default function HomePage() {
  return (
    <main className="min-h-screen overflow-hidden">
      <section className="relative min-h-screen px-4 py-6 sm:px-6 lg:px-8">
        <div className="pointer-events-none absolute left-[7%] top-24 size-2 bg-primary/70 shadow-[18px_18px_0_hsl(var(--primary)/0.25),36px_0_0_hsl(var(--foreground)/0.08)]" />
        <div className="pointer-events-none absolute right-[8%] top-36 size-3 border border-primary/40 bg-surface shadow-[12px_-12px_0_hsl(var(--primary)/0.12)]" />
        <nav className="container-dashboard glass-surface pixel-panel flex h-16 items-center justify-between px-4">
          <div className="flex items-center gap-3">
            <div className="flex size-9 items-center justify-center overflow-hidden border border-border bg-foreground">
              <Image src="/icon.svg" alt="Motionly" width={36} height={36} className="size-9" priority />
            </div>
            <span className="text-lg font-black">Motionly</span>
          </div>
          <div className="flex items-center gap-3">
            <ThemeMenu
              items={[
                { href: "/pricing", label: "Pricing" },
                { href: "/login", label: "Log in" },
                { href: "/signup", label: "Start free" },
              ]}
            />
            <ButtonLink href="/login" variant="ghost" className="hidden sm:inline-flex">
              Log in
            </ButtonLink>
            <ButtonLink href="/signup">
              Start free
              <ArrowRight className="ml-2 size-4" aria-hidden="true" />
            </ButtonLink>
          </div>
        </nav>

        <div className="container-dashboard grid min-h-[calc(100vh-5rem)] items-center gap-10 py-10 lg:grid-cols-[minmax(0,0.95fr)_minmax(420px,1.05fr)] lg:py-6">
          <div className="max-w-3xl">
            <div className="inline-flex items-center gap-2 border border-primary/30 bg-surface/80 px-3 py-2 text-xs font-black uppercase tracking-widest text-primary shadow-soft backdrop-blur">
              <span className="size-1.5 bg-primary" />
              Premium AI editing cockpit
            </div>
            <h1 className="mt-5 max-w-3xl text-4xl font-black leading-[1.02] sm:text-6xl">
              Make polished reels from rough footage.
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-8 text-muted-foreground sm:text-lg">
              Upload a talking-head clip, generate timed captions and an edit plan, add B-roll, music, and visual polish,
              then export a vertical MP4 for Instagram, TikTok, or Shorts.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <ButtonLink href="/signup" className="h-12 px-6">
                Start free
                <ArrowRight className="ml-2 size-4" aria-hidden="true" />
              </ButtonLink>
              <ButtonLink href="/pricing" variant="secondary" className="h-12 px-6">
                View pricing
              </ButtonLink>
            </div>
            <div className="mt-8 grid gap-3 text-sm text-muted-foreground sm:grid-cols-3">
              {["AI edit plans", "Caption timing", "Render pipeline"].map((item) => (
                <div key={item} className="pixel-panel flex items-center gap-2 px-3 py-2">
                  <CheckCircle2 className="size-4 shrink-0 text-primary" aria-hidden="true" />
                  {item}
                </div>
              ))}
            </div>
          </div>

          <div className="pixel-panel-strong pixel-corners pixel-glow relative mx-auto w-full max-w-[680px] overflow-hidden bg-surface/90 p-3 backdrop-blur">
            <span className="pixel-ornament -right-1 top-10" />
            <div className="grid min-h-[560px] gap-3 lg:grid-cols-[132px_minmax(0,1fr)_164px]">
              <aside className="hidden border border-border bg-background/80 p-2 lg:block">
                <div className="mb-3 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-muted-foreground">
                  <Clapperboard className="size-3" />
                  Media
                </div>
                {["Source", "B-roll", "Music", "Captions"].map((item, index) => (
                  <div key={item} className="mb-2 border border-border bg-surface p-2">
                    <div className="mb-2 h-2 w-12 bg-muted" />
                    <div className={index === 0 ? "aspect-video bg-foreground" : "h-8 bg-muted"} />
                  </div>
                ))}
              </aside>
              <section className="flex min-w-0 flex-col border border-border bg-foreground p-3 text-white">
                <div className="mb-3 flex items-center justify-between border-b border-white/10 pb-2 text-[10px] font-black uppercase tracking-widest text-white/60">
                  <span>Live Preview</span>
                  <Play className="size-4" aria-hidden="true" />
                </div>
                <div className="mx-auto flex min-h-0 w-full max-w-[260px] flex-1 items-center justify-center">
                  <div className="relative aspect-[9/16] max-h-[470px] w-full overflow-hidden border-2 border-white/10 bg-[linear-gradient(145deg,#071311_0%,#0f766e_48%,#f8fafc_130%)]">
                    <div className="absolute inset-4 border border-dashed border-white/10" />
                    <div className="absolute bottom-[16%] left-1/2 w-[78%] -translate-x-1/2 border border-white/15 bg-black/45 p-3 backdrop-blur">
                      <p className="text-xl font-black leading-tight">Captioned. Cut. Ready.</p>
                      <p className="mt-2 text-xs text-white/65">AI plan, captions, B-roll, look, render.</p>
                    </div>
                  </div>
                </div>
                <div className="mt-3 grid grid-cols-5 gap-1">
                  {Array.from({ length: 10 }).map((_, index) => (
                    <div key={index} className="h-3 bg-white/10 odd:bg-primary/70" />
                  ))}
                </div>
              </section>
              <aside className="hidden border border-border bg-background/80 p-2 lg:block">
                <div className="mb-3 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-muted-foreground">
                  <Wand2 className="size-3" />
                  AI tools
                </div>
                {["Command prompt", "Caption module", "Export queue"].map((item) => (
                  <div key={item} className="mb-3 border border-border bg-surface p-3">
                    <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">{item}</p>
                    <div className="mt-3 h-2 w-full bg-muted" />
                    <div className="mt-2 h-2 w-2/3 bg-primary/40" />
                  </div>
                ))}
              </aside>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
