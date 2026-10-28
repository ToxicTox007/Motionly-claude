"use client";

import { SubmitButton } from "@/components/submit-button";
import Link from "next/link";
import Image from "next/image";
import React, { useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Alert, Input } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { Loader2 } from "lucide-react";
import { login } from "../actions";
import { PageTitle } from "@/components/page-title";

function LoginFormContent() {
  const searchParams = useSearchParams();
  const errorParam = searchParams.get("error");
  const noticeParam = searchParams.get("notice");
  const nextParam = searchParams.get("next") ?? "/dashboard";

  // Fields are uncontrolled and validated from their submitted values:
  // autofill and typing before hydration set values without input events.
  const [emailError, setEmailError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const validateEmail = (val: string) => {
    if (!val) return "Email is required.";
    const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!regex.test(val)) return "Please enter a valid email address.";
    return null;
  };

  // Only presence is checked here: an existing account's password may predate
  // today's policy, and a login form should not advertise the rules.
  const validatePassword = (val: string) => {
    if (!val) return "Password is required.";
    return null;
  };

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    const form = new FormData(e.currentTarget);
    const isEmailValid = validateEmail(String(form.get("email") ?? ""));
    const isPasswordValid = validatePassword(
      String(form.get("password") ?? ""),
    );
    if (isEmailValid || isPasswordValid) {
      e.preventDefault();
      setEmailError(isEmailValid);
      setPasswordError(isPasswordValid);
    }
  };

  const errorMessage =
    errorParam === "rate_limited"
      ? "Too many attempts. Please try again shortly."
      : errorParam === "invalid_credentials"
        ? "Invalid email or password. Please try again."
        : errorParam
          ? "We could not complete that sign-in. Try again."
          : null;

  return (
    <form
      action={login}
      onSubmit={handleSubmit}
      className="w-full max-w-[420px] animate-fade-up"
      noValidate
    >
      {/* Logo */}
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

      <p className="text-xs font-semibold text-primary">Welcome back</p>
      <h1 className="mt-2 text-3xl font-bold tracking-tight text-foreground">
        Log in to Motionly
      </h1>

      {/* Notices & Errors */}
      {noticeParam === "account_created" && (
        <Alert tone="info" className="mt-5">
          Your account is ready. Log in to continue.
        </Alert>
      )}
      {errorMessage && (
        <Alert tone="danger" className="mt-5">
          {errorMessage}
        </Alert>
      )}

      <input type="hidden" name="next" value={nextParam} />

      {/* Email */}
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
          onBlur={(e) => setEmailError(validateEmail(e.currentTarget.value))}
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

      {/* Password */}
      <div className="mt-4 space-y-1.5">
        <label
          htmlFor="password"
          className="block text-xs font-semibold text-foreground"
        >
          Password
        </label>
        <Input
          id="password"
          name="password"
          type="password"
          required
          autoComplete="current-password"
          onChange={(e) => {
            if (passwordError)
              setPasswordError(validatePassword(e.target.value));
          }}
          onBlur={(e) =>
            setPasswordError(validatePassword(e.currentTarget.value))
          }
          aria-describedby={passwordError ? "password-error" : undefined}
          aria-invalid={Boolean(passwordError)}
          className={passwordError ? "border-danger" : ""}
        />
        {passwordError && (
          <p id="password-error" className="text-xs font-bold text-danger">
            {passwordError}
          </p>
        )}
      </div>

      <SubmitButton className="mt-6 w-full" pendingLabel="Logging in...">
        Log in
      </SubmitButton>

      <p className="mt-4 text-center text-sm text-muted-foreground">
        <Link
          href="/forgot-password"
          className="inline-flex min-h-10 items-center font-semibold text-primary hover:underline"
        >
          Forgot password?
        </Link>
      </p>

      <p className="mt-5 text-center text-sm text-muted-foreground">
        Need an account?{" "}
        <Link
          href={`/signup?next=${encodeURIComponent(nextParam)}`}
          className="inline-flex min-h-10 items-center font-semibold text-primary hover:underline"
        >
          Sign up
        </Link>
      </p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="grid min-h-screen bg-background lg:grid-cols-2">
      <PageTitle title="Log in" />
      {/* Left showcase panel (decorative) */}
      <div
        aria-hidden="true"
        className="relative m-4 hidden flex-col items-center justify-center overflow-hidden rounded-neu-2xl bg-surface p-12 shadow-neu-raised-lg lg:flex"
      >
        <div className="relative text-center">
          <div className="mb-6 inline-flex size-16 items-center justify-center rounded-neu-md bg-primary text-primary-foreground shadow-neu-glow">
            <Image
              src="/icon.svg"
              alt=""
              width={46}
              height={46}
              className="size-12"
            />
          </div>
          <h2 className="text-3xl font-bold tracking-tight text-foreground">
            Make polished reels.
          </h2>
          <p className="mx-auto mt-3 max-w-xs text-sm font-medium text-muted-foreground">
            Upload a clip. AI edits it. You download or share.
          </p>
          {/* Fake editor preview */}
          <div className="mx-auto mt-10 w-[260px] overflow-hidden rounded-neu-lg bg-surface text-left shadow-neu-raised">
            <div className="flex h-9 items-center gap-1.5 rounded-t-neu-lg bg-muted px-3 shadow-neu-inset-sm">
              {["bg-danger", "bg-warning", "bg-success"].map((c, i) => (
                <div key={i} className={`size-2 rounded-full ${c}`} />
              ))}
              <span className="ml-auto text-2xs font-semibold text-muted-foreground">
                Motionly
              </span>
            </div>
            <div className="space-y-2 p-3">
              {["AI Timeline", "Captions", "B-roll", "Music", "Export"].map(
                (item, i) => (
                  <div
                    key={item}
                    className={`flex h-8 items-center rounded-neu px-2.5 ${i === 0 ? "bg-primary text-primary-foreground shadow-neu-glow" : "bg-surface text-muted-foreground shadow-neu-raised-sm"}`}
                  >
                    <span className="text-2xs font-semibold">{item}</span>
                    {i === 0 && (
                      <PixelIcon name="play" className="ml-auto size-3" />
                    )}
                  </div>
                ),
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Right: form */}
      <div className="flex items-center justify-center bg-background px-6 py-16 sm:px-8">
        <Suspense
          fallback={
            <div className="flex size-16 items-center justify-center rounded-neu-md bg-surface shadow-neu-raised">
              <Loader2 className="size-6 animate-spin text-primary" />
            </div>
          }
        >
          <LoginFormContent />
        </Suspense>
      </div>
    </main>
  );
}
