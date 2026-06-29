"use client";

import React, { useState, useEffect, useRef } from "react";
import { useFormStatus } from "react-dom";
import { ArrowRight, Sparkles, Loader2 } from "lucide-react";
import { Button } from "@/components/button";
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
    if (inputRef.current) {
      inputRef.current.focus();
    }
  }, []);

  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setTitle(val);
    if (val && !val.trim()) {
      setError("Project name cannot contain only spaces.");
    } else {
      setError(null);
    }
  };

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    if (!title.trim()) {
      e.preventDefault();
      setError("Project name cannot be empty.");
      if (inputRef.current) {
        inputRef.current.focus();
      }
    }
  };

  return (
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

      <form
        action={createProject}
        onSubmit={handleSubmit}
        className="pixel-panel-strong pixel-corners space-y-8 p-6 sm:p-8"
      >
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
            placeholder="e.g. My Amazing Founder Story"
            value={title}
            onChange={handleTitleChange}
            aria-describedby={error ? "title-error" : undefined}
            className="pixel-input h-14 w-full px-5 text-lg font-medium transition-all hover:border-primary/40 focus:outline-none"
          />
          {error ? (
            <p id="title-error" className="text-xs font-bold text-danger mt-1">
              {error}
            </p>
          ) : null}
        </div>

        <SubmitButton />
      </form>

      <p className="mt-8 text-center text-sm text-muted-foreground">
        Step 1 of 4: Project setup
      </p>
    </div>
  );
}
