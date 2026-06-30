"use client";

import Link from "next/link";
import React, { useState, Suspense } from "react";
import { useFormStatus } from "react-dom";
import { useSearchParams } from "next/navigation";
import { Button } from "@/components/button";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import { env } from "@/lib/env";
import { signup, loginWithGoogle } from "../actions";

function PasswordStrength({ password }: { password: string }) {
  if (!password) return null;
  const score = password.length >= 12 ? 4 : password.length >= 10 ? 3 : password.length >= 8 ? 2 : 1;
  const labels = ["", "Weak", "Fair", "Strong", "Very strong"];
  const colors = ["", "bg-red-500", "bg-amber-500", "bg-emerald-400", "bg-emerald-500"];
  const textColors = ["", "text-red-400", "text-amber-400", "text-emerald-400", "text-emerald-400"];
  return (
    <div className="mt-2 space-y-1">
      <div className="flex gap-1">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className={`h-1.5 flex-1 rounded-sm border border-foreground transition-all duration-300 ${i <= score ? colors[score] : "bg-muted"}`}
          />
        ))}
      </div>
      <p className={`text-xs font-mono font-bold ${textColors[score]}`}>{labels[score]}</p>
    </div>
  );
}

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending}
      className="mt-6 w-full inline-flex items-center justify-center gap-2 h-11 rounded-md bg-primary text-primary-foreground text-sm font-bold border-2 border-foreground transition-all duration-[100ms] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none focus:outline-none"
    >
      {pending ? (
        <>
          <Loader2 className="size-4 animate-spin" />
          Creating workspace…
        </>
      ) : (
        "Create workspace"
      )}
    </button>
  );
}

function SignupFormContent() {
  const searchParams = useSearchParams();
  const errorParam = searchParams.get("error");

  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const validateName = (val: string) => (!val.trim() ? "Name is required." : null);
  const validateEmail = (val: string) => {
    if (!val) return "Email is required.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val)) return "Please enter a valid email address.";
    return null;
  };
  const validatePassword = (val: string) => {
    if (!val) return "Password is required.";
    if (val.length < 8) return "Password must be at least 8 characters.";
    return null;
  };

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    const n = validateName(name);
    const em = validateEmail(email);
    const pw = validatePassword(password);
    if (n || em || pw) {
      e.preventDefault();
      setNameError(n);
      setEmailError(em);
      setPasswordError(pw);
    }
  };

  const errorMessage = errorParam === "rate_limited"
    ? "Email confirmation is temporarily rate limited. Wait a minute, then try again or log in if your account already exists."
    : errorParam
      ? "We could not create that account. Check the details and try again."
      : null;

  return (
    <form
      action={signup}
      onSubmit={handleSubmit}
      className="w-full max-w-[420px] animate-fade-up"
      noValidate
    >
      {/* Logo */}
      <Link href="/" className="mb-8 inline-flex items-center gap-2.5 group">
        <div className="flex size-8 items-center justify-center rounded border-2 border-foreground bg-primary shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
          <span className="text-sm font-black text-primary-foreground">M</span>
        </div>
        <span className="font-black uppercase tracking-tight text-base text-foreground">Motionly</span>
      </Link>

      <p className="text-xs font-black uppercase tracking-wider text-muted-foreground font-mono">
        Create account
      </p>
      <h1 className="mt-2 text-3xl font-black uppercase tracking-tight text-foreground">
        Start your workspace
      </h1>

      {errorMessage && (
        <div className="mt-5 flex items-start gap-2.5 rounded border-2 border-foreground bg-danger p-3.5 text-sm text-danger-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" strokeWidth={2.5} aria-hidden="true" />
          <span className="font-bold">{errorMessage}</span>
        </div>
      )}

      {/* Name */}
      <div className="mt-6 space-y-1.5">
        <label htmlFor="name" className="block text-xs font-black uppercase tracking-wider text-foreground">
          Full name
        </label>
        <input
          id="name"
          name="name"
          type="text"
          required
          value={name}
          onChange={(e) => { setName(e.target.value); if (nameError) setNameError(validateName(e.target.value)); }}
          onBlur={() => setNameError(validateName(name))}
          aria-describedby={nameError ? "name-error" : undefined}
          aria-invalid={Boolean(nameError)}
          className={`flex min-h-11 w-full rounded-md border-2 border-foreground bg-surface-elevated px-4 py-2 text-base font-bold transition-all hover:border-primary focus:bg-surface focus:shadow-[3px_3px_0px_#000] dark:focus:shadow-[3px_3px_0px_#fff] focus:outline-none sm:text-sm text-foreground ${nameError ? "border-danger" : ""}`}
        />
        {nameError && (
          <p id="name-error" className="text-xs font-bold text-danger">{nameError}</p>
        )}
      </div>

      {/* Email */}
      <div className="mt-4 space-y-1.5">
        <label htmlFor="email" className="block text-xs font-black uppercase tracking-wider text-foreground">
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          required
          value={email}
          onChange={(e) => { setEmail(e.target.value); if (emailError) setEmailError(validateEmail(e.target.value)); }}
          onBlur={() => setEmailError(validateEmail(email))}
          aria-describedby={emailError ? "email-error" : undefined}
          aria-invalid={Boolean(emailError)}
          className={`flex min-h-11 w-full rounded-md border-2 border-foreground bg-surface-elevated px-4 py-2 text-base font-bold transition-all hover:border-primary focus:bg-surface focus:shadow-[3px_3px_0px_#000] dark:focus:shadow-[3px_3px_0px_#fff] focus:outline-none sm:text-sm text-foreground ${emailError ? "border-danger" : ""}`}
        />
        {emailError && (
          <p id="email-error" className="text-xs font-bold text-danger">{emailError}</p>
        )}
      </div>

      {/* Password */}
      <div className="mt-4 space-y-1.5">
        <label htmlFor="password" className="block text-xs font-black uppercase tracking-wider text-foreground">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          required
          value={password}
          onChange={(e) => { setPassword(e.target.value); if (passwordError) setPasswordError(validatePassword(e.target.value)); }}
          onBlur={() => setPasswordError(validatePassword(password))}
          aria-describedby={passwordError ? "password-error" : "password-hint"}
          aria-invalid={Boolean(passwordError)}
          className={`flex min-h-11 w-full rounded-md border-2 border-foreground bg-surface-elevated px-4 py-2 text-base font-bold transition-all hover:border-primary focus:bg-surface focus:shadow-[3px_3px_0px_#000] dark:focus:shadow-[3px_3px_0px_#fff] focus:outline-none sm:text-sm text-foreground ${passwordError ? "border-danger" : ""}`}
        />
        {passwordError ? (
          <p id="password-error" className="text-xs font-bold text-danger">{passwordError}</p>
        ) : (
          <p id="password-hint" className="text-xs font-mono text-muted-foreground">At least 8 characters.</p>
        )}
        <PasswordStrength password={password} />
      </div>

      <SubmitButton />

      {env.googleOAuthEnabled && (
        <button
          formAction={loginWithGoogle}
          formNoValidate
          className="mt-3 w-full inline-flex items-center justify-center gap-2.5 h-11 rounded-md bg-surface border-2 border-foreground text-sm font-bold text-foreground transition-all duration-[100ms] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none focus:outline-none"
        >
          <svg className="size-4" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
          </svg>
          Continue with Google
        </button>
      )}

      <p className="mt-5 text-center text-sm text-muted-foreground font-mono">
        Already have an account?{" "}
        <Link href="/login" className="font-bold text-foreground hover:underline">
          Log in
        </Link>
      </p>
    </form>
  );
}

export default function SignupPage() {
  if (typeof window !== "undefined" && window.process?.env?.NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE === "true") {
    return null;
  }

  return (
    <main className="min-h-screen grid lg:grid-cols-2">
      {/* Left decorative panel */}
      <div className="relative hidden lg:flex flex-col items-center justify-center overflow-hidden bg-surface border-r-4 border-foreground">
        <div className="relative text-center px-12">
          <h2 className="text-3xl font-black uppercase tracking-tight text-foreground">Your reel pipeline awaits.</h2>
          <p className="mt-3 text-muted-foreground max-w-xs font-mono text-xs">
            Create your workspace and start transforming footage with AI in minutes.
          </p>
          <div className="mt-10 space-y-3 max-w-[280px] mx-auto text-left">
            {[
              "AI-populated timelines",
              "Word-perfect auto-captions",
              "Smart B-roll indexing",
              "1-click vertical export",
            ].map((feature) => (
              <div key={feature} className="flex items-center gap-2.5 text-sm font-bold text-foreground font-mono">
                <CheckCircle2 className="size-4 shrink-0 text-primary" strokeWidth={2.5} aria-hidden="true" />
                {feature}
              </div>
            ))}
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
          <SignupFormContent />
        </Suspense>
      </div>
    </main>
  );
}
