"use client";

import React, { useEffect, useRef, useState } from "react";
import { useFormStatus } from "react-dom";
import { ArrowRight, Loader2 } from "lucide-react";
import { Button } from "@/components/button";
import { Badge } from "@/components/brutal-ui";
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

export function NewProjectClient() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setTitle(val);
    setError(val && !val.trim() ? "Project name cannot contain only spaces." : null);
  };

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    if (!title.trim()) {
      e.preventDefault();
      setError("Project name cannot be empty.");
      inputRef.current?.focus();
    }
  };

  return (
    <div className="mx-auto max-w-2xl py-8 sm:py-12">
      <div className="mb-8 space-y-4 text-center">
        <div className="inline-flex size-16 items-center justify-center rounded border-2 border-foreground bg-primary text-primary-foreground shadow-brutal">
          <PixelIcon name="new" className="size-8" />
        </div>
        <Badge variant="primary">Step 1 / Launch terminal</Badge>
        <h2 className="text-4xl font-black uppercase">Name the reel</h2>
        <p className="text-base font-bold text-muted-foreground">
          Create the project record, then continue directly to upload.
        </p>
      </div>

      <form
        action={createProject}
        onSubmit={handleSubmit}
        className="pixel-panel-strong space-y-8 p-6 sm:p-8"
      >
        <div className="grid gap-2 border-2 border-foreground bg-background p-3 font-mono text-xs font-black uppercase sm:grid-cols-3">
          <span className="text-primary">01 Name</span>
          <span className="text-muted-foreground">02 Upload</span>
          <span className="text-muted-foreground">03 AI edit</span>
        </div>
        <div className="space-y-3">
          <label
            className="text-sm font-bold uppercase tracking-widest text-muted-foreground/70"
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
            value={title}
            onChange={handleTitleChange}
            aria-describedby={error ? "title-error" : undefined}
            className="pixel-input h-14 w-full px-5 text-lg font-medium transition-all hover:border-primary/40 focus:outline-none"
          />
          {error ? (
            <p id="title-error" className="mt-1 text-xs font-bold text-danger">
              {error}
            </p>
          ) : null}
        </div>

        <SubmitButton />
      </form>

      <p className="mt-8 text-center font-mono text-sm font-bold text-muted-foreground">
        The next screen keeps upload validation and accepted file rules unchanged.
      </p>
    </div>
  );
}
