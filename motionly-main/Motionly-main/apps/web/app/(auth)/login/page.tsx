"use client";

import Link from "next/link";
import Image from "next/image";
import React, { useState, Suspense } from "react";
import { useFormStatus } from "react-dom";
import { useSearchParams } from "next/navigation";
import { Alert, Input } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { Loader2 } from "lucide-react";
import { login } from "../actions";
import { PageTitle } from "@/components/page-title";

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending}
      className="mt-6 inline-flex h-11 w-full items-center justify-center gap-2 rounded border-2 border-foreground bg-primary text-sm font-black uppercase text-primary-foreground shadow-brutal transition-all duration-[100ms] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-brutal-lg active:translate-x-0 active:translate-y-0 active:shadow-none disabled:cursor-not-allowed disabled:opacity-50 disabled:transform-none disabled:shadow-none focus:outline-none"
    >
      {pending ? (
        <>
          <Loader2 className="size-4 animate-spin" />
          Logging in...
        </>
      ) : (
        "Log in"
      )}
    </button>
  );
}

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

  const validatePassword = (val: string) => {
    if (!val) return "Password is required.";
    if (val.length < 8) return "Password must be at least 8 characters.";
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
    errorParam === "email_not_confirmed"
      ? "Confirm your email address before logging in."
      : errorParam === "rate_limited"
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
        <div className="flex size-9 items-center justify-center rounded border-2 border-foreground bg-primary shadow-brutal-sm">
          <Image
            src="/icon.svg"
            alt="Motionly"
            width={28}
            height={28}
            className="size-7"
            priority
          />
        </div>
        <span className="text-base font-black uppercase text-foreground">
          Motionly
        </span>
      </Link>

      <p className="text-xs font-black uppercase tracking-wider text-muted-foreground font-mono">
        Welcome back
      </p>
      <h1 className="mt-2 text-3xl font-black uppercase tracking-tight text-foreground">
        Log in to Motionly
      </h1>

      {/* Notices & Errors */}
      {noticeParam === "check_email" && (
        <Alert tone="info" className="mt-5">
          Check your email to confirm the account, then log in.
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
          className="block text-xs font-black uppercase tracking-wider text-foreground"
        >
          Email
        </label>
        <Input
          id="email"
          name="email"
          type="email"
          required
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
          className="block text-xs font-black uppercase tracking-wider text-foreground"
        >
          Password
        </label>
        <Input
          id="password"
          name="password"
          type="password"
          required
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

      <SubmitButton />

      <p className="mt-5 text-center text-sm text-muted-foreground font-mono">
        Need an account?{" "}
        <Link
          href="/signup"
          className="inline-flex min-h-10 items-center font-bold text-foreground hover:underline"
        >
          Sign up
        </Link>
      </p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="min-h-screen grid lg:grid-cols-2">
      <PageTitle title="Log in" />
      {/* Left decorative panel */}
      <div className="relative hidden flex-col items-center justify-center overflow-hidden border-r-4 border-foreground bg-surface lg:flex">
        <div className="relative text-center px-12">
          <div className="mb-6 inline-flex size-16 items-center justify-center rounded border-2 border-foreground bg-primary shadow-brutal">
            <Image
              src="/icon.svg"
              alt=""
              width={46}
              height={46}
              className="size-12"
            />
          </div>
          <h2 className="text-3xl font-black uppercase tracking-tight text-foreground">
            Make polished reels.
          </h2>
          <p className="mt-3 text-muted-foreground max-w-xs font-mono text-xs">
            Upload a clip. AI edits it. You post.
          </p>
          {/* Fake editor preview */}
          <div className="mt-10 w-[260px] rounded border-2 border-foreground bg-background overflow-hidden shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] mx-auto text-left">
            <div className="flex h-8 items-center gap-1.5 border-b-2 border-foreground px-3 bg-surface-elevated">
              {["bg-red-400", "bg-amber-400", "bg-green-400"].map((c, i) => (
                <div
                  key={i}
                  className={`size-2 rounded border border-foreground ${c}`}
                />
              ))}
              <span className="ml-auto text-[9px] font-bold text-muted-foreground font-mono">
                Motionly
              </span>
            </div>
            <div className="p-3 space-y-2">
              {["AI Timeline", "Captions", "B-roll", "Music", "Export"].map(
                (item, i) => (
                  <div
                    key={item}
                    className={`h-7 rounded border-2 border-foreground flex items-center px-2.5 ${i === 0 ? "bg-primary text-primary-foreground shadow-[1px_1px_0px_#000]" : "bg-surface"}`}
                  >
                    <span
                      className={`text-[9px] font-bold ${i === 0 ? "text-primary-foreground" : "text-muted-foreground"}`}
                    >
                      {item}
                    </span>
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
      <div className="flex items-center justify-center px-6 py-16 sm:px-8 bg-background">
        <Suspense
          fallback={
            <div className="flex size-16 items-center justify-center rounded border-2 border-foreground bg-surface shadow-[3px_3px_0px_#000]">
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
