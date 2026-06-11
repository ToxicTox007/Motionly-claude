import { Check } from "lucide-react";
import { Button, ButtonLink } from "@/components/button";
import { startCheckout } from "@/app/(app)/billing/actions";
import { fallbackBillingStatus } from "@/lib/billing";
import { env } from "@/lib/env";

export default async function PricingPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  await searchParams;

  return (
    <main className="container-content min-h-screen py-12">
      <div className="pixel-panel pixel-corners flex flex-wrap items-end justify-between gap-4 p-5">
        <div>
          <p className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Pricing
          </p>
          <h1 className="mt-3 text-4xl font-black">Plans for consistent creators.</h1>
          <p className="mt-3 max-w-2xl text-muted-foreground">
            Start watermarked, upgrade when B-roll, higher limits, priority queueing, or team
            workflows become part of your reel pipeline.
          </p>
        </div>
        <ButtonLink href="/signup">Create account</ButtonLink>
      </div>

      <div className="mt-8 grid gap-4 lg:grid-cols-4">
        {fallbackBillingStatus.plans.map((plan) => (
          <section key={plan.key} className="pixel-panel p-6">
            <h2 className="text-xl font-bold">{plan.name}</h2>
            <p className="mt-4 text-4xl font-black">{formatPrice(plan.price_cents)}</p>
            <p className="text-sm text-muted-foreground">per month</p>
            <ul className="mt-6 space-y-3 text-sm">
              {plan.features.map((feature) => (
                <li key={feature} className="flex gap-2">
                  <Check className="mt-0.5 size-4 text-primary" aria-hidden="true" />
                  {feature}
                </li>
              ))}
            </ul>
            {plan.key === "free" ? (
              <ButtonLink href="/signup" variant="secondary" className="mt-6 w-full">
                Start free
              </ButtonLink>
            ) : env.billingEnabled ? (
              <form action={startCheckout} className="mt-6">
                <input type="hidden" name="plan" value={plan.key} />
                <Button type="submit" className="w-full">
                  Upgrade
                </Button>
              </form>
            ) : (
              <Button disabled className="mt-6 w-full">
                Coming soon
              </Button>
            )}
          </section>
        ))}
      </div>
    </main>
  );
}

function formatPrice(cents: number) {
  if (cents === 0) return "$0";
  return `$${Math.round(cents / 100)}`;
}
