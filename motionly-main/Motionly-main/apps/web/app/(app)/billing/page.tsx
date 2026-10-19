import { ArrowUpRight, Gauge, Settings } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/button";
import { Alert } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { getBillingStatus } from "@/lib/billing";
import { env } from "@/lib/env";
import { usageMeter } from "@/lib/usage-meter.mjs";
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
  const meter = usageMeter(status.usage);

  return (
    <AppShell title="Billing">
      <div className="space-y-5">
        <Alert
          tone="warning"
          title={
            env.billingEnabled
              ? "Billing Sandbox Preview (Demo Mode)"
              : "Billing Integration Offline"
          }
        >
          {env.billingEnabled
            ? "Stripe billing is running in sandbox/test mode. Upgrades will execute in a secure test sandbox and real credit cards will not be charged."
            : "Stripe billing integration is currently disabled. All workspace plans listed below are mock/demo accounts only."}
        </Alert>
        {status.subscription.payment_issue ? (
          <Alert tone="danger" title="Payment issue">
            Update your payment method to keep future premium renders and
            actions available.
          </Alert>
        ) : null}
        {error ? (
          <Alert tone="danger" title="Billing action failed">
            {error.replaceAll("_", " ")}
          </Alert>
        ) : null}

        <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          {/* Current plan card */}
          <div className="card-elevated p-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-sm font-bold uppercase text-muted-foreground">
                  Current plan
                </p>
                <h2 className="mt-2 text-3xl font-black">
                  {status.current_plan.name}
                </h2>
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
                    Manage sandbox billing
                  </Button>
                </form>
              ) : (
                <Button type="button" variant="secondary" disabled>
                  <Settings className="mr-2 size-4" aria-hidden="true" />
                  Billing unavailable
                </Button>
              )}
            </div>

            <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Metric
                label="Resolution"
                value={status.entitlements.max_resolution}
              />
              <Metric
                label="Watermark"
                value={status.entitlements.watermark ? "On" : "Off"}
              />
              <Metric
                label="B-roll"
                value={status.entitlements.broll ? "Included" : "Locked"}
              />
              <Metric label="Seats" value={String(status.entitlements.seats)} />
            </div>
          </div>

          {/* Usage card */}
          <div className="card rounded-xl p-5">
            <div className="flex items-center gap-2">
              <Gauge className="size-5 text-primary" aria-hidden="true" />
              <h2 className="text-lg font-bold">Monthly usage</h2>
            </div>
            <div className="mt-5 flex items-end justify-between gap-4">
              <p className="text-4xl font-black">{status.usage.renders_used}</p>
              <p className="pb-1 text-sm font-semibold text-muted-foreground">
                {meter.metered
                  ? `of ${status.usage.renders_limit} renders`
                  : "renders · unlimited"}
              </p>
            </div>
            {meter.metered && meter.ariaLabel ? (
              <div
                className="progress-bar mt-4 h-2.5"
                role="progressbar"
                aria-label={meter.ariaLabel}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={meter.percent ?? 0}
              >
                <div
                  className="progress-fill h-full"
                  style={{ width: `${meter.percent}%` }}
                />
              </div>
            ) : null}
            <p className="mt-3 text-sm text-muted-foreground">
              {meter.remainingLabel}
            </p>
          </div>
        </section>

        {/* Plan cards */}
        <section className="grid gap-4 lg:grid-cols-4">
          {status.plans.map((plan) => {
            const isCurrent = plan.key === status.current_plan.key;
            return (
              <div
                key={plan.key}
                className={cn(
                  "card rounded-xl p-5 flex flex-col",
                  isCurrent
                    ? "border-primary/50 bg-primary/5"
                    : "border-border",
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-lg font-bold flex flex-wrap items-center gap-1.5">
                      {plan.name}
                      <span className="badge badge-warning text-[9px]">
                        Mock Plan
                      </span>
                    </h3>
                    <p className="text-4xl font-black mt-2">
                      {formatPrice(plan.price_cents)}
                    </p>
                    <p className="text-sm text-muted-foreground">per month</p>
                  </div>
                  {isCurrent ? <span className="badge">Current</span> : null}
                </div>
                <ul className="mt-5 space-y-2 text-sm flex-1">
                  {plan.features.map((feature) => (
                    <li key={feature} className="flex gap-2">
                      <PixelIcon
                        name="success"
                        className="mt-0.5 size-4 shrink-0 text-primary"
                      />
                      <span>{feature}</span>
                    </li>
                  ))}
                </ul>
                {plan.key !== "free" && !isCurrent && env.billingEnabled ? (
                  <form action={startCheckout} className="mt-5">
                    <input type="hidden" name="plan" value={plan.key} />
                    <Button type="submit" className="w-full">
                      <ArrowUpRight
                        className="mr-2 size-4"
                        aria-hidden="true"
                      />
                      Preview checkout
                    </Button>
                  </form>
                ) : plan.key !== "free" && !isCurrent ? (
                  <Button type="button" disabled className="mt-5 w-full">
                    <ArrowUpRight className="mr-2 size-4" aria-hidden="true" />
                    Billing unavailable
                  </Button>
                ) : null}
              </div>
            );
          })}
        </section>

        <section className="card rounded-xl p-5">
          <h2 className="text-lg font-bold">Operations notes</h2>
          <div className="mt-3 grid gap-3 text-sm text-muted-foreground md:grid-cols-3">
            <p>Paid upgrades are disabled until Stripe keys are configured.</p>
            <p>
              Downgrades keep old exports available and only gate future premium
              actions.
            </p>
            <p>
              Tax, VAT, refund, and dispute policies remain operational
              responsibilities.
            </p>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface-elevated p-3">
      <p className="text-xs font-bold uppercase text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-lg font-bold capitalize">{value}</p>
    </div>
  );
}

function formatPrice(cents: number) {
  if (cents === 0) return "$0";
  return `$${Math.round(cents / 100)}`;
}
