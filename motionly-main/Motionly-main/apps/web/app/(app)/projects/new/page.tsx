import { ArrowRight, Sparkles } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/button";
import { createProject } from "../actions";

export default function NewProjectPage() {
  return (
    <AppShell title="Create New Reel">
      <div className="max-w-xl mx-auto py-12 animate-in fade-in slide-in-from-bottom-8 duration-700">
        <div className="text-center space-y-4 mb-12">
          <div className="inline-flex size-16 items-center justify-center rounded-2xl bg-primary shadow-xl shadow-primary/20 text-primary-foreground transform -rotate-6">
            <Sparkles className="size-8" aria-hidden="true" />
          </div>
          <h2 className="text-4xl font-black tracking-tight">Ready to create?</h2>
          <p className="text-muted-foreground text-lg">
            Give your project a name to get started.
          </p>
        </div>

        <form action={createProject} className="space-y-8 rounded-3xl border border-border bg-surface p-10 shadow-xl ring-1 ring-black/5">
          <div className="space-y-3">
            <label className="text-sm font-bold uppercase tracking-widest text-muted-foreground/70" htmlFor="title">
              Project Name
            </label>
            <input
              id="title"
              name="title"
              required
              autoFocus
              maxLength={120}
              placeholder="e.g. My Amazing Founder Story"
              className="h-14 w-full rounded-2xl border border-border bg-background px-5 text-lg font-medium outline-none transition-all focus:ring-4 focus:ring-primary/10 focus:border-primary hover:border-muted-foreground/30"
            />
          </div>
          
          <Button type="submit" className="w-full h-14 rounded-2xl text-lg shadow-lg hover:scale-[1.02] active:scale-[0.98] transition-all">
            Continue to Upload
            <ArrowRight className="ml-2 size-5" />
          </Button>
        </form>

        <p className="mt-8 text-center text-sm text-muted-foreground">
          Step 1 of 4: Project setup
        </p>
      </div>
    </AppShell>
  );
}
