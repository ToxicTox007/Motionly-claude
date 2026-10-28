"use client";

import { SubmitButton } from "@/components/submit-button";
import Link from "next/link";
import Image from "next/image";
import React, { useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Alert, Input } from "@/components/brutal-ui";
import { Loader2 } from "lucide-react";
import { resetPassword } from "../actions";
import { PageTitle } from "@/components/page-title";
import { RESET_PASSWORD_ERROR_MESSAGES } from "@/lib/password-reset.mjs";
import { PASSWORD_MIN_LENGTH } from "@/lib/password-policy.mjs";

function ResetPasswordFormContent() {
  const searchParams = useSearchParams();
  const errorParam = searchParams.get("error");
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const validatePassword = (val: string) => {
    if (!val) return "Password is required.";
    if (val.length < PASSWORD_MIN_LENGTH)
      return `Password must be at least ${PASSWORD_MIN_LENGTH} characters.`;
    return null;
  };

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    const form = new FormData(e.currentTarget);
    const pw = validatePassword(String(form.get("password") ?? ""));
    if (pw) {
      e.preventDefault();
      setPasswordError(pw);
    }
  };

  const errorMessage =
    errorParam && Object.hasOwn(RESET_PASSWORD_ERROR_MESSAGES, errorParam)
      ? RESET_PASSWORD_ERROR_MESSAGES[
          errorParam as keyof typeof RESET_PASSWORD_ERROR_MESSAGES
        ]
      : errorParam
        ? RESET_PASSWORD_ERROR_MESSAGES.system_error
        : null;

  return (
    <form
      action={resetPassword}
      onSubmit={handleSubmit}
      className="w-full max-w-[420px] animate-fade-up"
      noValidate
    >
      <Link href="/" className="group mb-8 inline-flex items-center gap-2.5">
        <div className="flex size-9 items-center justify-center rounded-neu bg-primary text-primary-foreground shadow-neu-glow">
          <Image
            src="/icon.svg"
            alt=""
            width={28}
            height={28}
            className="size-7"
          />
        </div>
        <span className="text-base font-bold text-foreground">Motionly</span>
      </Link>

      <p className="text-xs font-semibold text-primary">
        Choose a new password
      </p>
      <h1 className="mt-2 text-3xl font-bold tracking-tight text-foreground">
        Set your new password
      </h1>

      {errorMessage && (
        <Alert tone="danger" className="mt-5">
          {errorMessage}
        </Alert>
      )}

      <div className="mt-6 space-y-1.5">
        <label
          htmlFor="password"
          className="block text-xs font-semibold text-foreground"
        >
          New password
        </label>
        <Input
          id="password"
          name="password"
          type="password"
          required
          autoComplete="new-password"
          onChange={(e) => {
            if (passwordError)
              setPasswordError(validatePassword(e.target.value));
          }}
          onBlur={(e) =>
            setPasswordError(validatePassword(e.currentTarget.value))
          }
          aria-describedby={passwordError ? "password-error" : "password-hint"}
          aria-invalid={Boolean(passwordError)}
          className={passwordError ? "border-danger" : ""}
        />
        {passwordError ? (
          <p id="password-error" className="text-xs font-bold text-danger">
            {passwordError}
          </p>
        ) : (
          <p
            id="password-hint"
            className="text-xs font-medium text-muted-foreground"
          >
            At least {PASSWORD_MIN_LENGTH} characters. Signing out of your other
            sessions.
          </p>
        )}
      </div>

      <SubmitButton className="mt-6 w-full" pendingLabel="Saving...">
        Set new password
      </SubmitButton>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <main className="min-h-screen flex items-center justify-center px-6 py-16 sm:px-8 bg-background">
      <PageTitle title="Reset password" />
      <Suspense
        fallback={
          <div className="flex size-16 items-center justify-center rounded-neu-md bg-surface shadow-neu-raised">
            <Loader2 className="size-6 animate-spin text-primary" />
          </div>
        }
      >
        <ResetPasswordFormContent />
      </Suspense>
    </main>
  );
}
