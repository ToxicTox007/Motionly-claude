import Link from "next/link";
import { redirect } from "next/navigation";
import { Button } from "@/components/button";
import { env } from "@/lib/env";
import { loginWithGoogle, signup } from "../actions";

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  if (process.env.NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE === "true") {
    redirect("/dashboard");
  }
  const params = await searchParams;

  return (
    <main className="grid min-h-screen place-items-center px-4 py-12 sm:px-6">
      <form action={signup} className="pixel-panel-strong pixel-corners glass-surface w-full max-w-md p-6 sm:p-7">
        <Link href="/" className="mb-8 inline-flex items-center gap-2 text-sm font-black">
          <span className="size-2 bg-primary shadow-[8px_0_0_hsl(var(--primary)/0.25)]" />
          Motionly
        </Link>
        <p className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Create account</p>
        <h1 className="mt-2 text-3xl font-black">Start your workspace</h1>
        {params.error ? (
          <p className="mt-4 border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
            {params.error === "rate_limited"
              ? "Email confirmation is temporarily rate limited. Wait a minute, then try again or log in if your account already exists."
              : "We could not create that account. Check the details and try again."}
          </p>
        ) : null}
        <label className="mt-6 block text-sm font-medium" htmlFor="name">
          Name
        </label>
        <input id="name" name="name" required className="pixel-input mt-2 h-11 w-full px-3" />
        <label className="mt-4 block text-sm font-medium" htmlFor="email">
          Email
        </label>
        <input id="email" name="email" type="email" required className="pixel-input mt-2 h-11 w-full px-3" />
        <label className="mt-4 block text-sm font-medium" htmlFor="password">
          Password
        </label>
        <input id="password" name="password" type="password" required minLength={8} className="pixel-input mt-2 h-11 w-full px-3" />
        <Button type="submit" className="mt-6 w-full">
          Create workspace
        </Button>
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
    </main>
  );
}
