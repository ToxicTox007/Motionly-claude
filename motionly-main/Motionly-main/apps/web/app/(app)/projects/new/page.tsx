import { ArrowRight, Sparkles } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/button";
import { createProject } from "../actions";

export default function NewProjectPage() {
  return (
    <AppShell title="Create New Reel">
      <div className="mx-auto max-w-xl py-8 sm:py-12">
        <div className="text-center space-y-4 mb-12">
          <div className="pixel-button inline-flex size-16 items-center justify-center bg-primary text-primary-foreground">
            <Sparkles className="size-8" aria-hidden="true" />
          </div>
          <h2 className="text-4xl font-black tracking-tight">Ready to create?</h2>
          <p className="text-muted-foreground text-lg">
            Give your project a name to get started.
          </p>
        </div>

        <form action={createProject} className="pixel-panel-strong pixel-corners space-y-8 p-6 sm:p-8">
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
              className="pixel-input h-14 w-full px-5 text-lg font-medium transition-all hover:border-primary/40"
            />
          </div>
          
          <Button type="submit" className="h-14 w-full text-lg">
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
