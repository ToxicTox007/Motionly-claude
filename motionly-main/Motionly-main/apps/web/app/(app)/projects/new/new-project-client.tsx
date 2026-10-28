"use client";

import React, { useEffect, useRef, useState } from "react";
import { useFormStatus } from "react-dom";
import { ArrowRight, Loader2 } from "lucide-react";
import { Button } from "@/components/button";
import { Alert, Badge } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { createProject } from "../actions";

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" className="h-14 w-full text-lg" disabled={pending}>
      {pending ? (
        <>
          <Loader2 className="mr-2 size-5 animate-spin" />
          Creating...
        </>
      ) : (
        <>
          Continue to Upload
          <ArrowRight className="ml-2 size-5" />
        </>
      )}
    </Button>
  );
}

export function NewProjectClient({
  serverError = null,
}: {
  /** Mapped copy for a failed create (?error= on /projects/new). */
  serverError?: string | null;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  // The field is uncontrolled and validated from its submitted value:
  // autofill and typing before hydration set values without input events.
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setError(
      val && !val.trim() ? "Project name cannot contain only spaces." : null,
    );
  };

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    const title = new FormData(e.currentTarget).get("title");
    if (!String(title ?? "").trim()) {
      e.preventDefault();
      setError("Project name cannot be empty.");
      inputRef.current?.focus();
    }
  };

  return (
    <div className="mx-auto max-w-2xl py-8 sm:py-12">
      <div className="mb-8 space-y-4 text-center">
        <div className="inline-flex size-16 items-center justify-center rounded-neu-md bg-primary text-primary-foreground shadow-neu-glow">
          <PixelIcon name="new" className="size-8" />
        </div>
        <Badge variant="primary">Step 1 / Launch terminal</Badge>
        <h2 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
          Name the reel
        </h2>
        <p className="text-base font-medium text-muted-foreground">
          Create the project record, then continue directly to upload.
        </p>
      </div>

      <form
        action={createProject}
        onSubmit={handleSubmit}
        className="space-y-8 rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg sm:p-8"
      >
        {serverError ? <Alert tone="danger">{serverError}</Alert> : null}
        <div className="grid gap-2 rounded-neu-md bg-surface p-3 text-xs font-semibold shadow-neu-inset-sm sm:grid-cols-3">
          <span className="text-primary">01 Name</span>
          <span className="text-muted-foreground">02 Upload</span>
          <span className="text-muted-foreground">03 AI edit</span>
        </div>
        <div className="space-y-3">
          <label
            className="text-sm font-semibold text-muted-foreground"
            htmlFor="title"
          >
            Project Name
          </label>
          <input
            ref={inputRef}
            id="title"
            name="title"
            required
            maxLength={120}
            placeholder="Founder story cutdown"
            onChange={handleTitleChange}
            aria-describedby={error ? "title-error" : undefined}
            className="min-h-14 w-full rounded-neu border border-control bg-surface px-5 text-lg font-medium text-foreground shadow-neu-inset-sm transition focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          />
          {error ? (
            <p
              id="title-error"
              className="mt-1 text-xs font-semibold text-danger"
            >
              {error}
            </p>
          ) : null}
        </div>

        <SubmitButton />
      </form>

      <p className="mt-8 text-center text-sm font-medium text-muted-foreground">
        The next screen keeps upload validation and accepted file rules
        unchanged.
      </p>
    </div>
  );
}
