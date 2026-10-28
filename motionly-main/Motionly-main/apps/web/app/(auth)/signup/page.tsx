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
import {
  SIGNUP_ERROR_MESSAGES,
  type SignupErrorReason,
} from "@/lib/signup.mjs";
import { PASSWORD_HINT, passwordFeedback } from "@/lib/password-feedback";

function PasswordStrength({
  password,
  email,
}: {
  password: string;
  email: string;
}) {
  if (!password) return null;
  const { score, label } = passwordFeedback(password, email);
  const colors = ["", "bg-danger", "bg-warning", "bg-success", "bg-primary"];
  const textColors = [
    "",
    "text-danger",
    "text-warning",
    "text-success",
    "text-primary",
  ];
  return (
    <div className="mt-2 space-y-1" aria-live="polite">
      <div className="flex gap-1" aria-hidden="true">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className={`h-1.5 flex-1 rounded-full transition-all duration-300 ${i <= score ? colors[score] : "bg-muted shadow-neu-inset-sm"}`}
          />
        ))}
      </div>
      <p className={`text-xs font-semibold ${textColors[score]}`}>
        Password strength: {label}
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
      className="mt-6 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-neu bg-primary text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed active:brightness-100 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-neu-raised-sm focus:outline-none"
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
  // Where to land after signing up (e.g. back to /invite). Defaults to the
  // dashboard; the server re-validates it with safeNextPath.
  const nextParam = searchParams.get("next") ?? "/dashboard";

  // Fields are uncontrolled and validated from their submitted values:
  // autofill and typing before hydration set values without input events.
  const [nameError, setNameError] = useState<string | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  // Shown by the strength meter only; validation reads the submitted value.
  const [typedPassword, setTypedPassword] = useState("");
  const [typedEmail, setTypedEmail] = useState("");

  const validateName = (val: string) =>
    !val.trim() ? "Name is required." : null;
  const validateEmail = (val: string) => {
    if (!val) return "Email is required.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val))
      return "Please enter a valid email address.";
    return null;
  };
  // The same policy the server applies (lib/password-policy.mjs).
  const validatePassword = (val: string, email: string) => {
    if (!val) return "Password is required.";
    return passwordFeedback(val, email).problem;
  };

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    const form = new FormData(e.currentTarget);
    const n = validateName(String(form.get("name") ?? ""));
    const em = validateEmail(String(form.get("email") ?? ""));
    const pw = validatePassword(
      String(form.get("password") ?? ""),
      String(form.get("email") ?? ""),
    );
    if (n || em || pw) {
      e.preventDefault();
      setNameError(n);
      setEmailError(em);
      setPasswordError(pw);
    }
  };

  const errorMessage = errorParam
    ? Object.hasOwn(SIGNUP_ERROR_MESSAGES, errorParam)
      ? SIGNUP_ERROR_MESSAGES[errorParam as SignupErrorReason]
      : SIGNUP_ERROR_MESSAGES.system_error
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

      <p className="text-xs font-semibold text-primary">Create account</p>
      <h1 className="mt-2 text-3xl font-bold tracking-tight text-foreground">
        Start your workspace
      </h1>

      {errorMessage && (
        <Alert tone="danger" className="mt-5">
          {errorMessage}
        </Alert>
      )}

      <input type="hidden" name="next" value={nextParam} />

      {/* Name */}
      <div className="mt-6 space-y-1.5">
        <label
          htmlFor="name"
          className="block text-xs font-semibold text-foreground"
        >
          Full name
        </label>
        <Input
          id="name"
          name="name"
          type="text"
          required
          autoComplete="name"
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
            setTypedEmail(e.target.value);
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
          autoComplete="new-password"
          onChange={(e) => {
            setTypedPassword(e.target.value);
            if (passwordError)
              setPasswordError(validatePassword(e.target.value, typedEmail));
          }}
          onBlur={(e) =>
            setPasswordError(
              validatePassword(e.currentTarget.value, typedEmail),
            )
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
            {PASSWORD_HINT}
          </p>
        )}
        <PasswordStrength password={typedPassword} email={typedEmail} />
      </div>

      <SubmitButton />

      <p className="mt-5 text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link
          href={`/login?next=${encodeURIComponent(nextParam)}`}
          className="inline-flex min-h-10 items-center font-semibold text-primary hover:underline"
        >
          Log in
        </Link>
      </p>
    </form>
  );
}

export default function SignupPage() {
  return (
    <main className="grid min-h-screen bg-background lg:grid-cols-2">
      <PageTitle title="Sign up" />
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
            Your reel pipeline awaits.
          </h2>
          <p className="mx-auto mt-3 max-w-xs text-sm font-medium text-muted-foreground">
            Create your workspace and start transforming footage with AI in
            minutes.
          </p>
          <div className="mx-auto mt-10 max-w-[280px] space-y-3 text-left">
            {[
              "AI-populated timelines",
              "Word-perfect auto-captions",
              "Smart B-roll indexing",
              "1-click vertical export",
            ].map((feature) => (
              <div
                key={feature}
                className="flex items-center gap-2.5 rounded-neu-md bg-surface px-3 py-2 text-sm font-semibold text-foreground shadow-neu-raised-sm"
              >
                <span className="flex size-6 shrink-0 items-center justify-center rounded-neu-sm bg-surface text-primary shadow-neu-inset-sm">
                  <PixelIcon name="success" className="size-3.5" />
                </span>
                {feature}
              </div>
            ))}
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
          <SignupFormContent />
        </Suspense>
      </div>
    </main>
  );
}
