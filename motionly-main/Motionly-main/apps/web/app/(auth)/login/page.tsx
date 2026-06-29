"use client";

import Link from "next/link";
import React, { useState, use, Suspense } from "react";
import { useFormStatus } from "react-dom";
import { useSearchParams } from "next/navigation";
import { Button } from "@/components/button";
import { Loader2 } from "lucide-react";
import { env } from "@/lib/env";
import { login, loginWithGoogle } from "../actions";

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" className="mt-6 w-full" disabled={pending}>
      {pending ? (
        <>
          <Loader2 className="mr-2 size-4 animate-spin" />
          Logging in...
        </>
      ) : (
        "Log in"
      )}
    </Button>
  );
}

function LoginFormContent() {
  const searchParams = useSearchParams();
  const errorParam = searchParams.get("error");
  const noticeParam = searchParams.get("notice");
  const nextParam = searchParams.get("next") ?? "/dashboard";

  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);

  const [password, setPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const validateEmail = (val: string) => {
    if (!val) {
      return "Email is required.";
    }
    const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!regex.test(val)) {
      return "Please enter a valid email address.";
    }
    return null;
  };

  const validatePassword = (val: string) => {
    if (!val) {
      return "Password is required.";
    }
    if (val.length < 8) {
      return "Password must be at least 8 characters.";
    }
    return null;
  };

  const handleEmailChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setEmail(val);
    if (emailError) {
      setEmailError(validateEmail(val));
    }
  };

  const handleEmailBlur = () => {
    setEmailError(validateEmail(email));
  };

  const handlePasswordChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setPassword(val);
    if (passwordError) {
      setPasswordError(validatePassword(val));
    }
  };

  const handlePasswordBlur = () => {
    setPasswordError(validatePassword(password));
  };

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    const isEmailValid = validateEmail(email);
    const isPasswordValid = validatePassword(password);

    if (isEmailValid || isPasswordValid) {
      e.preventDefault();
      setEmailError(isEmailValid);
      setPasswordError(isPasswordValid);
    }
  };

  return (
    <form
      action={login}
      onSubmit={handleSubmit}
      className="pixel-panel-strong pixel-corners glass-surface w-full max-w-md p-6 sm:p-7"
    >
      <Link href="/" className="mb-8 inline-flex items-center gap-2 text-sm font-black">
        <span className="size-2 bg-primary shadow-[8px_0_0_hsl(var(--primary)/0.25)]" />
        Motionly
      </Link>
      <p className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Welcome back</p>
      <h1 className="mt-2 text-3xl font-black">Log in to Motionly</h1>

      {noticeParam === "check_email" ? (
        <p className="mt-4 border border-border bg-muted px-3 py-2 text-sm text-muted-foreground font-bold">
          ✉️ Check your email to confirm the account, then log in.
        </p>
      ) : null}

      {errorParam === "email_not_confirmed" ? (
        <p className="mt-4 border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger font-bold">
          ⚠️ Confirm your email address before logging in.
        </p>
      ) : errorParam === "rate_limited" ? (
        <p className="mt-4 border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger font-bold">
          ⚠️ Too many attempts. Please try again shortly.
        </p>
      ) : errorParam === "invalid_credentials" ? (
        <p className="mt-4 border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger font-bold">
          ⚠️ Invalid email or password. Please try again.
        </p>
      ) : errorParam ? (
        <p className="mt-4 border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger font-bold">
          ⚠️ We could not complete that sign-in. Try again.
        </p>
      ) : null}

      <input type="hidden" name="next" value={nextParam} />

      <div className="mt-6">
        <label className="block text-sm font-bold uppercase tracking-wider text-muted-foreground/80" htmlFor="email">
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          required
          value={email}
          onChange={handleEmailChange}
          onBlur={handleEmailBlur}
          aria-describedby={emailError ? "email-error" : undefined}
          className="pixel-input mt-2 h-11 w-full px-3 focus:outline-none"
        />
        {emailError ? (
          <p id="email-error" className="text-xs font-bold text-danger mt-1">
            {emailError}
          </p>
        ) : null}
      </div>

      <div className="mt-4">
        <label className="block text-sm font-bold uppercase tracking-wider text-muted-foreground/80" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          required
          value={password}
          onChange={handlePasswordChange}
          onBlur={handlePasswordBlur}
          aria-describedby={passwordError ? "password-error" : undefined}
          className="pixel-input mt-2 h-11 w-full px-3 focus:outline-none"
        />
        {passwordError ? (
          <p id="password-error" className="text-xs font-bold text-danger mt-1">
            {passwordError}
          </p>
        ) : null}
      </div>

      <SubmitButton />

      {env.googleOAuthEnabled ? (
        <Button formAction={loginWithGoogle} formNoValidate variant="secondary" className="mt-3 w-full">
          Continue with Google
        </Button>
      ) : null}

      <p className="mt-4 text-center text-sm text-muted-foreground">
        Need an account?{" "}
        <Link href="/signup" className="font-semibold text-foreground">
          Sign up
        </Link>
      </p>
    </form>
  );
}

export default function LoginPage() {
  if (typeof window !== "undefined" && window.process?.env?.NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE === "true") {
    return null;
  }

  return (
    <main className="grid min-h-screen place-items-center px-4 py-12 sm:px-6">
      <Suspense fallback={
        <div className="pixel-panel flex size-16 items-center justify-center">
          <Loader2 className="size-8 animate-spin text-primary" />
        </div>
      }>
        <LoginFormContent />
      </Suspense>
    </main>
  );
}
