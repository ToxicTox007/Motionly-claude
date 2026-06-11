import Link from "next/link";
import { redirect } from "next/navigation";
import { Button } from "@/components/button";
import { env } from "@/lib/env";
import { login, loginWithGoogle } from "../actions";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; next?: string; notice?: string }>;
}) {
  if (process.env.NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE === "true") {
    redirect("/dashboard");
  }
  const params = await searchParams;

  return (
    <main className="grid min-h-screen place-items-center px-4 py-12 sm:px-6">
      <form action={login} className="pixel-panel-strong pixel-corners w-full max-w-md p-6 sm:p-7">
        <Link href="/" className="mb-8 inline-flex items-center gap-2 text-sm font-black">
          <span className="size-2 bg-primary shadow-[8px_0_0_hsl(var(--primary)/0.25)]" />
          Motionly
        </Link>
        <p className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Welcome back</p>
        <h1 className="mt-2 text-3xl font-black">Log in to Motionly</h1>
        {params.notice === "check_email" ? (
          <p className="mt-4 border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
            Check your email to confirm the account, then log in.
          </p>
        ) : null}
        {params.error === "email_not_confirmed" ? (
          <p className="mt-4 border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
            Confirm your email address before logging in.
          </p>
        ) : params.error ? (
          <p className="mt-4 border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
            We could not complete that sign-in. Try again.
          </p>
        ) : null}
        <input type="hidden" name="next" value={params.next ?? "/dashboard"} />
        <label className="mt-6 block text-sm font-medium" htmlFor="email">
          Email
        </label>
        <input id="email" name="email" type="email" required className="pixel-input mt-2 h-11 w-full px-3" />
        <label className="mt-4 block text-sm font-medium" htmlFor="password">
          Password
        </label>
        <input id="password" name="password" type="password" required className="pixel-input mt-2 h-11 w-full px-3" />
        <Button type="submit" className="mt-6 w-full">
          Log in
        </Button>
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
    </main>
  );
}
