import { Check } from "lucide-react";
import { Button, ButtonLink } from "@/components/button";
import { startCheckout } from "@/app/(app)/billing/actions";
import { fallbackBillingStatus } from "@/lib/billing";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

export default async function PricingPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  await searchParams;

  // Identify featured plan: most expensive non-free plan
  const paidPlans = fallbackBillingStatus.plans.filter((p) => p.price_cents > 0);
  const featuredKey =
    paidPlans.length > 0
      ? paidPlans.reduce((a, b) => (b.price_cents > a.price_cents ? b : a)).key
      : null;

  return (
    <main className="container-content min-h-screen py-16">
      {/* Page header — centered */}
      <div className="text-center mb-12">
        <span className="badge badge-primary mb-4 inline-flex">Pricing</span>
        <h1 className="text-5xl font-black tracking-tight sm:text-6xl mt-4">
          Plans for consistent creators.
        </h1>
        <p className="mt-4 max-w-2xl mx-auto text-muted-foreground">
          Start watermarked, upgrade when B-roll, higher limits, priority queueing, or team
          workflows become part of your reel pipeline.
        </p>
      </div>

      {/* Plan cards grid */}
      <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4 items-start">
        {fallbackBillingStatus.plans.map((plan) => {
          const isFeatured = plan.key === featuredKey;
          return (
            <section
              key={plan.key}
              className={cn(
                "card rounded-2xl p-6 flex flex-col relative",
                isFeatured && "border-primary/50 bg-primary/5 shadow-glow",
              )}
            >
              {isFeatured ? (
                <span className="badge badge-primary absolute -top-3 right-4">
                  Most Popular
                </span>
              ) : null}

              <h2 className="text-xl font-bold">{plan.name}</h2>
              <p className="text-5xl font-black mt-3">{formatPrice(plan.price_cents)}</p>
              <p className="text-sm text-muted-foreground">per month</p>

              {/* Divider */}
              <div className="h-px bg-border my-5" />

              <ul className="space-y-3 text-sm flex-1">
                {plan.features.map((feature) => (
                  <li key={feature} className="flex gap-2">
                    <Check
                      className="mt-0.5 size-4 text-primary shrink-0"
                      strokeWidth={1.5}
                      aria-hidden="true"
                    />
                    {feature}
                  </li>
                ))}
              </ul>

              <div className="mt-6">
                {plan.key === "free" ? (
                  <ButtonLink href="/signup" variant="secondary" className="w-full">
                    Start free
                  </ButtonLink>
                ) : env.billingEnabled ? (
                  <form action={startCheckout}>
                    <input type="hidden" name="plan" value={plan.key} />
                    <Button type="submit" className="w-full">
                      Upgrade
                    </Button>
                  </form>
                ) : (
                  <Button disabled className="w-full">
                    Coming soon
                  </Button>
                )}
              </div>
            </section>
          );
        })}
      </div>
    </main>
  );
}

function formatPrice(cents: number) {
  if (cents === 0) return "$0";
  return `$${Math.round(cents / 100)}`;
}
