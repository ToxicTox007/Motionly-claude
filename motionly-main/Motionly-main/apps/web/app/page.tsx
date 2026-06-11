import { ArrowRight, CheckCircle2, Play } from "lucide-react";
import { ButtonLink } from "@/components/button";

export default function HomePage() {
  return (
    <main className="min-h-screen">
      <section className="grid min-h-[88vh] content-center gap-10 px-6 py-10 lg:grid-cols-[1fr_520px] lg:items-center lg:px-12">
        <div className="max-w-3xl">
          <p className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Motionly</p>
          <h1 className="mt-4 max-w-3xl text-5xl font-black leading-[1.02] sm:text-6xl">
            Make polished reels from rough footage.
          </h1>
          <p className="mt-5 max-w-2xl text-lg leading-8 text-muted-foreground">
            Upload a talking-head clip, generate timed captions and an edit plan, add B-roll, music, and visual polish,
            then export a vertical MP4 for Instagram, TikTok, or Shorts.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <ButtonLink href="/signup">
              Start free
              <ArrowRight className="ml-2 size-4" aria-hidden="true" />
            </ButtonLink>
            <ButtonLink href="/pricing" variant="secondary">
              View pricing
            </ButtonLink>
          </div>
          <div className="mt-8 grid gap-3 text-sm text-muted-foreground sm:grid-cols-3">
            {["Workspace model", "RLS-first database", "Health-checked API"].map((item) => (
              <div key={item} className="flex items-center gap-2">
                <CheckCircle2 className="size-4 text-primary" aria-hidden="true" />
                {item}
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-lg border border-border bg-foreground p-3 shadow-xl">
          <div className="aspect-[9/16] rounded-md bg-[linear-gradient(145deg,#111827_0%,#0f766e_48%,#f8fafc_100%)] p-5 text-white">
            <div className="flex items-center justify-between text-xs text-white/70">
              <span>Preview</span>
              <Play className="size-4" aria-hidden="true" />
            </div>
            <div className="mt-44 rounded-lg bg-black/30 p-4 backdrop-blur-sm">
              <p className="text-2xl font-black leading-tight">Captioned, cut, and ready to post.</p>
              <p className="mt-3 text-sm text-white/75">
                One workspace for upload, AI planning, media polish, export, and versions.
              </p>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
