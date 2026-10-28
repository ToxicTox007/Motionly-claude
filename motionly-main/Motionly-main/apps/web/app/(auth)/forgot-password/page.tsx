"use client";

import Link from "next/link";
import Image from "next/image";
import React, { useState, Suspense } from "react";
import { useFormStatus } from "react-dom";
import { useSearchParams } from "next/navigation";
import { Alert, Input } from "@/components/brutal-ui";
import { Loader2 } from "lucide-react";
import { requestPasswordReset } from "../actions";
import { PageTitle } from "@/components/page-title";
import {
  RESET_REQUEST_CONFIRMATION,
  RESET_REQUEST_ERROR_MESSAGES,
} from "@/lib/password-reset.mjs";

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending}
      className="mt-6 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-neu bg-primary text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed active:brightness-100 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-neu-raised-sm focus:outline-none"
    >
      {pending ? (
        <>
          <Loader2 className="size-4 animate-spin" />
          Sending...
        </>
      ) : (
        "Request reset"
      )}
    </button>
  );
}

function ForgotPasswordFormContent() {
  const searchParams = useSearchParams();
  const sent = searchParams.get("sent") === "1";
  const errorParam = searchParams.get("error");
  const [emailError, setEmailError] = useState<string | null>(null);

  const validateEmail = (val: string) => {
    if (!val) return "Email is required.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val))
      return "Please enter a valid email address.";
    return null;
  };

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    const form = new FormData(e.currentTarget);
    const em = validateEmail(String(form.get("email") ?? ""));
    if (em) {
      e.preventDefault();
      setEmailError(em);
    }
  };

  const errorMessage =
    errorParam && Object.hasOwn(RESET_REQUEST_ERROR_MESSAGES, errorParam)
      ? RESET_REQUEST_ERROR_MESSAGES[
          errorParam as keyof typeof RESET_REQUEST_ERROR_MESSAGES
        ]
      : errorParam
        ? RESET_REQUEST_ERROR_MESSAGES.system_error
        : null;

  return (
    <div className="w-full max-w-[420px] animate-fade-up">
      <Link href="/" className="group mb-8 inline-flex items-center gap-2.5">
        <div className="flex size-9 items-center justify-center rounded-neu bg-primary text-primary-foreground shadow-neu-glow">
          <Image
            src="/icon.svg"
            alt="Motionly"
            width={28}
            height={28}
            className="size-7"
            priority
          />
        </div>
        <span className="text-base font-bold text-foreground">Motionly</span>
      </Link>

      <p className="text-xs font-semibold text-primary">
        Forgot your password?
      </p>
      <h1 className="mt-2 text-3xl font-bold tracking-tight text-foreground">
        Get back into Motionly
      </h1>

      {sent ? (
        <Alert tone="info" className="mt-5">
          {RESET_REQUEST_CONFIRMATION}
        </Alert>
      ) : (
        <form action={requestPasswordReset} onSubmit={handleSubmit} noValidate>
          {errorMessage && (
            <Alert tone="danger" className="mt-5">
              {errorMessage}
            </Alert>
          )}
          <p className="mt-5 text-sm text-muted-foreground">
            Enter your email. If an account exists, an admin will set up a way
            for you to get back in.
          </p>
          <div className="mt-6 space-y-1.5">
            <label
              htmlFor="email"
              className="block text-xs font-semibold text-foreground"
            >
              Email
            </label>
            <Input
              id="email"
              name="email"
              type="email"
              required
              autoComplete="email"
              onChange={(e) => {
                if (emailError) setEmailError(validateEmail(e.target.value));
              }}
              onBlur={(e) =>
                setEmailError(validateEmail(e.currentTarget.value))
              }
              aria-describedby={emailError ? "email-error" : undefined}
              aria-invalid={Boolean(emailError)}
              className={emailError ? "border-danger" : ""}
            />
            {emailError && (
              <p id="email-error" className="text-xs font-bold text-danger">
                {emailError}
              </p>
            )}
          </div>
          <SubmitButton />
        </form>
      )}

      <p className="mt-5 text-center text-sm text-muted-foreground">
        Remembered it?{" "}
        <Link
          href="/login"
          className="inline-flex min-h-10 items-center font-semibold text-primary hover:underline"
        >
          Back to log in
        </Link>
      </p>
    </div>
  );
}

export default function ForgotPasswordPage() {
  return (
    <main className="min-h-screen flex items-center justify-center px-6 py-16 sm:px-8 bg-background">
      <PageTitle title="Forgot password" />
      <Suspense
        fallback={
          <div className="flex size-16 items-center justify-center rounded-neu-md bg-surface shadow-neu-raised">
            <Loader2 className="size-6 animate-spin text-primary" />
          </div>
        }
      >
        <ForgotPasswordFormContent />
      </Suspense>
    </main>
  );
}
