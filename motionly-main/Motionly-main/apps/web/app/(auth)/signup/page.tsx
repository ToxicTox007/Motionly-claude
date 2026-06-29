"use client";

import Link from "next/link";
import React, { useState, Suspense } from "react";
import { useFormStatus } from "react-dom";
import { useSearchParams } from "next/navigation";
import { Button } from "@/components/button";
import { Loader2 } from "lucide-react";
import { env } from "@/lib/env";
import { signup, loginWithGoogle } from "../actions";

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" className="mt-6 w-full" disabled={pending}>
      {pending ? (
        <>
          <Loader2 className="mr-2 size-4 animate-spin" />
          Creating workspace...
        </>
      ) : (
        "Create workspace"
      )}
    </Button>
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

  const validateName = (val: string) => {
    if (!val.trim()) {
      return "Name is required.";
    }
    return null;
  };

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

  const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setName(val);
    if (nameError) {
      setNameError(validateName(val));
    }
  };

  const handleNameBlur = () => {
    setNameError(validateName(name));
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
    const isNameValid = validateName(name);
    const isEmailValid = validateEmail(email);
    const isPasswordValid = validatePassword(password);

    if (isNameValid || isEmailValid || isPasswordValid) {
      e.preventDefault();
      setNameError(isNameValid);
      setEmailError(isEmailValid);
      setPasswordError(isPasswordValid);
    }
  };

  return (
    <form
      action={signup}
      onSubmit={handleSubmit}
      className="pixel-panel-strong pixel-corners glass-surface w-full max-w-md p-6 sm:p-7"
    >
      <Link href="/" className="mb-8 inline-flex items-center gap-2 text-sm font-black">
        <span className="size-2 bg-primary shadow-[8px_0_0_hsl(var(--primary)/0.25)]" />
        Motionly
      </Link>
      <p className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Create account</p>
      <h1 className="mt-2 text-3xl font-black">Start your workspace</h1>

      {errorParam === "rate_limited" ? (
        <p className="mt-4 border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger font-bold">
          ⚠️ Email confirmation is temporarily rate limited. Wait a minute, then try again or log in if your account already exists.
        </p>
      ) : errorParam ? (
        <p className="mt-4 border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger font-bold">
          ⚠️ We could not create that account. Check the details and try again.
        </p>
      ) : null}

      <div className="mt-6">
        <label className="block text-sm font-bold uppercase tracking-wider text-muted-foreground/80" htmlFor="name">
          Name
        </label>
        <input
          id="name"
          name="name"
          type="text"
          required
          value={name}
          onChange={handleNameChange}
          onBlur={handleNameBlur}
          aria-describedby={nameError ? "name-error" : undefined}
          className="pixel-input mt-2 h-11 w-full px-3 focus:outline-none"
        />
        {nameError ? (
          <p id="name-error" className="text-xs font-bold text-danger mt-1">
            {nameError}
          </p>
        ) : null}
      </div>

      <div className="mt-4">
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
        ) : (
          <p className="text-[11px] text-muted-foreground mt-1">
            Password must be at least 8 characters long.
          </p>
        )}
      </div>

      <SubmitButton />

      {env.googleOAuthEnabled ? (
        <Button formAction={loginWithGoogle} formNoValidate variant="secondary" className="mt-3 w-full">
          Continue with Google
        </Button>
      ) : null}

      <p className="mt-4 text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/login" className="font-semibold text-foreground">
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
    <main className="grid min-h-screen place-items-center px-4 py-12 sm:px-6">
      <Suspense fallback={
        <div className="pixel-panel flex size-16 items-center justify-center">
          <Loader2 className="size-8 animate-spin text-primary" />
        </div>
      }>
        <SignupFormContent />
      </Suspense>
    </main>
  );
}
