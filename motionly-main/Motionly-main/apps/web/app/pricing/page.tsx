import { Button, ButtonLink } from "@/components/button";
import { Alert, Badge, Card } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { startCheckout } from "@/app/(app)/billing/actions";
import { fallbackBillingStatus } from "@/lib/billing";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";
import { PageTitle } from "@/components/page-title";

export default async function PricingPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  await searchParams;

  // Identify featured plan: most expensive non-free plan
  const paidPlans = fallbackBillingStatus.plans.filter(
    (p) => p.price_cents > 0,
  );
  const featuredKey =
    paidPlans.length > 0
      ? paidPlans.reduce((a, b) => (b.price_cents > a.price_cents ? b : a)).key
      : null;

  return (
    <main className="container-content min-h-screen py-12 sm:py-16">
      <PageTitle title="Pricing" />
      <div className="mb-10 text-center">
        <Badge variant="primary">Pricing</Badge>
        <h1 className="mt-4 text-5xl font-black uppercase sm:text-6xl">
          Plans for reel production.
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-base font-bold text-muted-foreground">
          Start watermarked, upgrade when B-roll, higher limits, priority
          queueing, or team workflows become part of your reel pipeline.
        </p>
      </div>

      {!env.billingEnabled ? (
        <Alert
          tone="warning"
          title="Billing checkout is offline"
          className="mb-8"
        >
          Plan cartridges are visible for comparison, but checkout buttons stay
          disabled until billing keys are configured.
        </Alert>
      ) : null}

      <div className="grid items-start gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {fallbackBillingStatus.plans.map((plan) => {
          const isFeatured = plan.key === featuredKey;
          return (
            <Card
              key={plan.key}
              className={cn(
                "relative flex min-h-[28rem] flex-col p-5",
                isFeatured &&
                  "bg-primary text-primary-foreground shadow-brutal-lg",
              )}
            >
              {isFeatured ? (
                <Badge variant="accent" className="absolute -top-3 right-4">
                  Popular
                </Badge>
              ) : null}

              <h2 className="text-xl font-black uppercase">{plan.name}</h2>
              <p className="mt-3 text-5xl font-black">
                {formatPrice(plan.price_cents)}
              </p>
              <p
                className={cn(
                  "text-sm font-bold",
                  isFeatured ? "text-ink" : "text-muted-foreground",
                )}
              >
                per month
              </p>

              <div className="my-5 h-1 bg-foreground" />

              <ul className="space-y-3 text-sm flex-1">
                {plan.features.map((feature) => (
                  <li key={feature} className="flex gap-2 font-bold">
                    <PixelIcon
                      name="success"
                      className="mt-0.5 size-4 shrink-0"
                    />
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>

              <div className="mt-6">
                {plan.key === "free" ? (
                  <ButtonLink
                    href="/signup"
                    variant="secondary"
                    className="w-full"
                  >
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
            </Card>
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
