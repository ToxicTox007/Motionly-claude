import { AlertTriangle, ArrowUpRight, Check, Gauge, Settings } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/button";
import { getBillingStatus } from "@/lib/billing";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";
import { openCustomerPortal, startCheckout } from "./actions";

export default async function BillingPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = (await searchParams) ?? {};
  const error = typeof params.error === "string" ? params.error : null;
  const status = await getBillingStatus();

  return (
    <AppShell title="Billing">
      <div className="space-y-5">
        {status.subscription.payment_issue ? (
          <Banner
            tone="danger"
            icon={AlertTriangle}
            title="Payment issue"
            body="Update your payment method to keep future premium renders and actions available."
          />
        ) : null}
        {error ? (
          <Banner
            tone="danger"
            icon={AlertTriangle}
            title="Billing action failed"
            body={error.replaceAll("_", " ")}
          />
        ) : null}
        <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="pixel-panel pixel-corners p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-sm font-bold uppercase text-muted-foreground">Current plan</p>
                <h2 className="mt-2 text-3xl font-black">{status.current_plan.name}</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  {status.subscription.status === "none"
                    ? "Free workspace"
                    : `Subscription ${status.subscription.status.replaceAll("_", " ")}`}
                </p>
              </div>
              {env.billingEnabled ? (
                <form action={openCustomerPortal}>
                  <Button type="submit" variant="secondary">
                    <Settings className="mr-2 size-4" aria-hidden="true" />
                    Manage
                  </Button>
                </form>
              ) : (
                <Button type="button" variant="secondary" disabled>
                  <Settings className="mr-2 size-4" aria-hidden="true" />
                  Disabled
                </Button>
              )}
            </div>

            <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Metric label="Resolution" value={status.entitlements.max_resolution} />
              <Metric label="Watermark" value={status.entitlements.watermark ? "On" : "Off"} />
              <Metric label="B-roll" value={status.entitlements.broll ? "Included" : "Locked"} />
              <Metric label="Seats" value={String(status.entitlements.seats)} />
            </div>
          </div>

          <div className="pixel-panel p-5">
            <div className="flex items-center gap-2">
              <Gauge className="size-5 text-primary" aria-hidden="true" />
              <h2 className="text-lg font-bold">Monthly usage</h2>
            </div>
            <div className="mt-5 flex items-end justify-between gap-4">
              <p className="text-4xl font-black">{status.usage.renders_used}</p>
              <p className="pb-1 text-sm font-semibold text-muted-foreground">
                of {status.usage.renders_limit} renders
              </p>
            </div>
            <div className="pixel-progress mt-4 h-2">
              <div
                className="pixel-progress-fill h-full"
                style={{ width: `${status.usage.percent_used}%` }}
              />
            </div>
            <p className="mt-3 text-sm text-muted-foreground">
              {status.usage.remaining_renders} remaining this period.
            </p>
          </div>
        </section>

        <section className="grid gap-4 lg:grid-cols-4">
          {status.plans.map((plan) => {
            const isCurrent = plan.key === status.current_plan.key;
            return (
              <div
                key={plan.key}
                className={cn(
                  "pixel-panel p-5",
                  isCurrent ? "border-primary" : "border-border",
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-lg font-bold">{plan.name}</h3>
                    <p className="mt-2 text-3xl font-black">{formatPrice(plan.price_cents)}</p>
                    <p className="text-sm text-muted-foreground">per month</p>
                  </div>
                  {isCurrent ? (
                    <span className="border border-border bg-muted px-3 py-1 text-xs font-bold text-muted-foreground">
                      Current
                    </span>
                  ) : null}
                </div>
                <ul className="mt-5 space-y-2 text-sm">
                  {plan.features.map((feature) => (
                    <li key={feature} className="flex gap-2">
                      <Check className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
                      <span>{feature}</span>
                    </li>
                  ))}
                </ul>
                {plan.key !== "free" && !isCurrent && env.billingEnabled ? (
                  <form action={startCheckout} className="mt-5">
                    <input type="hidden" name="plan" value={plan.key} />
                    <Button type="submit" className="w-full">
                      <ArrowUpRight className="mr-2 size-4" aria-hidden="true" />
                      Upgrade
                    </Button>
                  </form>
                ) : plan.key !== "free" && !isCurrent ? (
                  <Button type="button" disabled className="mt-5 w-full">
                    <ArrowUpRight className="mr-2 size-4" aria-hidden="true" />
                    Coming soon
                  </Button>
                ) : null}
              </div>
            );
          })}
        </section>

        <section className="pixel-panel p-5">
          <h2 className="text-lg font-bold">Operations notes</h2>
          <div className="mt-3 grid gap-3 text-sm text-muted-foreground md:grid-cols-3">
            <p>Paid upgrades are disabled until Stripe keys are configured.</p>
            <p>Downgrades keep old exports available and only gate future premium actions.</p>
            <p>Tax, VAT, refund, and dispute policies remain operational responsibilities.</p>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border bg-surface p-3 shadow-soft">
      <p className="text-xs font-bold uppercase text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-bold capitalize">{value}</p>
    </div>
  );
}

function Banner({
  icon: Icon,
  title,
  body,
  tone,
}: {
  icon: typeof AlertTriangle;
  title: string;
  body: string;
  tone: "danger" | "neutral";
}) {
  return (
    <div
      className={cn(
        "flex gap-3 border p-4 shadow-soft",
        tone === "danger"
          ? "border-red-200 bg-red-50 text-red-900"
          : "border-border bg-muted text-muted-foreground",
      )}
    >
      <Icon className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-bold">{title}</p>
        <p className="mt-1 text-sm">{body}</p>
      </div>
    </div>
  );
}

function formatPrice(cents: number) {
  if (cents === 0) return "$0";
  return `$${Math.round(cents / 100)}`;
}
