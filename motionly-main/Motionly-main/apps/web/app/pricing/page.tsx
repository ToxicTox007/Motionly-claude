import { Button, ButtonLink } from "@/components/button";
import { Alert, Badge, Card } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { startCheckout } from "@/app/(app)/billing/actions";
import { getPublicPlans } from "@/lib/plans";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";
import { PageTitle } from "@/components/page-title";

export default async function PricingPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  await searchParams;

  // B33: plans come from the API (single source of truth). If the API is down
  // we show an error state rather than fabricating prices.
  const plansResult = await getPublicPlans();
  const plans = plansResult.ok ? plansResult.plans : [];

  // Identify featured plan: most expensive non-free plan
  const paidPlans = plans.filter((p) => p.price_paise > 0);
  const featuredKey =
    paidPlans.length > 0
      ? paidPlans.reduce((a, b) => (b.price_paise > a.price_paise ? b : a)).key
      : null;

  return (
    <main className="container-content min-h-screen py-12 sm:py-16">
      <PageTitle title="Pricing" />
      <div className="mb-10 text-center">
        <Badge variant="primary">Pricing</Badge>
        <h1 className="mt-4 text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
          Plans for reel production.
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-base font-medium text-muted-foreground">
          Start watermarked, upgrade when B-roll, higher limits, priority
          queueing, or team workflows become part of your reel pipeline.
        </p>
      </div>

      {!plansResult.ok ? (
        <Alert
          tone="danger"
          title="Plans are temporarily unavailable"
          className="mb-8"
        >
          We couldn&apos;t load the current plans and prices. Please refresh in
          a moment — we won&apos;t show prices we can&apos;t confirm.
        </Alert>
      ) : !env.billingEnabled ? (
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
        {plans.map((plan) => {
          const isFeatured = plan.key === featuredKey;
          return (
            <Card
              key={plan.key}
              className={cn(
                "relative flex min-h-[28rem] flex-col p-6",
                isFeatured && "shadow-neu-glow ring-2 ring-primary/40",
              )}
            >
              {isFeatured ? (
                <Badge variant="accent" className="absolute -top-3 right-4">
                  Popular
                </Badge>
              ) : null}

              <h2 className="text-xl font-bold tracking-tight text-foreground">
                {plan.name}
              </h2>
              <p className="mt-3 text-4xl font-extrabold tabular-nums text-foreground">
                {formatPrice(plan.price_paise)}
              </p>
              <p className="text-sm font-medium text-muted-foreground">
                per month
              </p>

              <div className="my-5 h-px bg-hairline" />

              <ul className="flex-1 space-y-3 text-sm">
                {plan.features.map((feature) => (
                  <li
                    key={feature}
                    className="flex gap-2.5 font-medium text-foreground"
                  >
                    <span className="flex size-5 shrink-0 items-center justify-center rounded-neu-sm bg-surface text-primary shadow-neu-inset-sm">
                      <PixelIcon name="success" className="size-3" />
                    </span>
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

function formatPrice(paise: number) {
  if (!paise) return "₹0";
  return `₹${Math.round(paise / 100)}`;
}
