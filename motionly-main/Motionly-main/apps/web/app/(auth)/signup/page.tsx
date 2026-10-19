"use client";

import Link from "next/link";
import Image from "next/image";
import React, { useState, Suspense } from "react";
import { useFormStatus } from "react-dom";
import { useSearchParams } from "next/navigation";
import { Alert, Input } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { Loader2 } from "lucide-react";
import { signup } from "../actions";
import { PageTitle } from "@/components/page-title";

function PasswordStrength({ password }: { password: string }) {
  if (!password) return null;
  const score =
    password.length >= 12
      ? 4
      : password.length >= 10
        ? 3
        : password.length >= 8
          ? 2
          : 1;
  const labels = ["", "Weak", "Fair", "Strong", "Very strong"];
  const colors = [
    "",
    "bg-red-500",
    "bg-amber-500",
    "bg-emerald-400",
    "bg-emerald-500",
  ];
  const textColors = [
    "",
    "text-red-400",
    "text-amber-400",
    "text-emerald-400",
    "text-emerald-400",
  ];
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
      <p className={`text-xs font-mono font-bold ${textColors[score]}`}>
        {labels[score]}
      </p>
    </div>
  );
}

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
          Creating workspace...
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

  // Fields are uncontrolled and validated from their submitted values:
  // autofill and typing before hydration set values without input events.
  const [nameError, setNameError] = useState<string | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  // Shown by the strength meter only; validation reads the submitted value.
  const [typedPassword, setTypedPassword] = useState("");

  const validateName = (val: string) =>
    !val.trim() ? "Name is required." : null;
  const validateEmail = (val: string) => {
    if (!val) return "Email is required.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val))
      return "Please enter a valid email address.";
    return null;
  };
  const validatePassword = (val: string) => {
    if (!val) return "Password is required.";
    if (val.length < 8) return "Password must be at least 8 characters.";
    return null;
  };

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    const form = new FormData(e.currentTarget);
    const n = validateName(String(form.get("name") ?? ""));
    const em = validateEmail(String(form.get("email") ?? ""));
    const pw = validatePassword(String(form.get("password") ?? ""));
    if (n || em || pw) {
      e.preventDefault();
      setNameError(n);
      setEmailError(em);
      setPasswordError(pw);
    }
  };

  const errorMessage =
    errorParam === "rate_limited"
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
        Create account
      </p>
      <h1 className="mt-2 text-3xl font-black uppercase tracking-tight text-foreground">
        Start your workspace
      </h1>

      {errorMessage && (
        <Alert tone="danger" className="mt-5">
          {errorMessage}
        </Alert>
      )}

      {/* Name */}
      <div className="mt-6 space-y-1.5">
        <label
          htmlFor="name"
          className="block text-xs font-black uppercase tracking-wider text-foreground"
        >
          Full name
        </label>
        <Input
          id="name"
          name="name"
          type="text"
          required
          onChange={(e) => {
            if (nameError) setNameError(validateName(e.target.value));
          }}
          onBlur={(e) => setNameError(validateName(e.currentTarget.value))}
          aria-describedby={nameError ? "name-error" : undefined}
          aria-invalid={Boolean(nameError)}
          className={nameError ? "border-danger" : ""}
        />
        {nameError && (
          <p id="name-error" className="text-xs font-bold text-danger">
            {nameError}
          </p>
        )}
      </div>

      {/* Email */}
      <div className="mt-4 space-y-1.5">
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
            setTypedPassword(e.target.value);
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
            className="text-xs font-mono text-muted-foreground"
          >
            At least 8 characters.
          </p>
        )}
        <PasswordStrength password={typedPassword} />
      </div>

      <SubmitButton />

      <p className="mt-5 text-center text-sm text-muted-foreground font-mono">
        Already have an account?{" "}
        <Link
          href="/login"
          className="inline-flex min-h-10 items-center font-bold text-foreground hover:underline"
        >
          Log in
        </Link>
      </p>
    </form>
  );
}

export default function SignupPage() {
  return (
    <main className="min-h-screen grid lg:grid-cols-2">
      <PageTitle title="Sign up" />
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
            Your reel pipeline awaits.
          </h2>
          <p className="mt-3 text-muted-foreground max-w-xs font-mono text-xs">
            Create your workspace and start transforming footage with AI in
            minutes.
          </p>
          <div className="mt-10 space-y-3 max-w-[280px] mx-auto text-left">
            {[
              "AI-populated timelines",
              "Word-perfect auto-captions",
              "Smart B-roll indexing",
              "1-click vertical export",
            ].map((feature) => (
              <div
                key={feature}
                className="flex items-center gap-2.5 text-sm font-bold text-foreground font-mono"
              >
                <PixelIcon
                  name="success"
                  className="size-4 shrink-0 text-primary"
                />
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
