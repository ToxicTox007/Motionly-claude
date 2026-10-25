import { ArrowUpRight, Gauge, XCircle } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/button";
import { Alert } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { getBillingStatus, getPaymentHistory } from "@/lib/billing";
import { getWorkspaceApiContext, canManageBilling } from "@/lib/api-auth";
import { env } from "@/lib/env";
import { usageMeter } from "@/lib/usage-meter.mjs";
import { pastDueGraceMessage } from "@/lib/payment-history.mjs";
import { cn } from "@/lib/utils";
import { cancelSubscription, retryRenewal, startCheckout } from "./actions";
import { PaymentHistory } from "./PaymentHistory";

const DEMO_NOTICE = "Demo payment — no money is charged";

export default async function BillingPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = (await searchParams) ?? {};
  const error = typeof params.error === "string" ? params.error : null;
  const status = await getBillingStatus();
  const meter = usageMeter(status.usage);
  // Members and admins can view plan/usage but only the owner manages billing.
  const context = await getWorkspaceApiContext();
  const isOwner = context ? canManageBilling(context.role) : false;
  // Payment history and in-app receipts are owner-only.
  const receipts = isOwner ? await getPaymentHistory() : [];

  return (
    <AppShell title="Billing">
      <div className="space-y-5">
        <Alert
          tone="warning"
          title={
            env.billingEnabled ? "Demo billing" : "Billing Integration Offline"
          }
        >
          {env.billingEnabled
            ? `${DEMO_NOTICE}. Upgrades run through a demo payment gateway in INR; no real card is charged.`
            : "Billing is currently disabled. All workspace plans listed below are demo accounts only."}
        </Alert>
        {status.subscription.payment_issue ? (
          <Alert tone="danger" title="Payment past due">
            Your last renewal failed.{" "}
            {env.billingEnabled ? DEMO_NOTICE + "." : ""}{" "}
            {pastDueGraceMessage(status.subscription.past_due_grace_ends_at)}
            {isOwner && env.billingEnabled ? (
              <form action={retryRenewal} className="mt-3">
                <Button type="submit" variant="secondary">
                  Retry payment
                </Button>
              </form>
            ) : null}
          </Alert>
        ) : null}
        {error ? (
          <Alert tone="danger" title="Billing action failed">
            {error.replaceAll("_", " ")}
          </Alert>
        ) : null}
        {!isOwner ? (
          <Alert tone="info" title="Only the owner can manage billing">
            You can view the workspace plan and usage, but only the workspace
            owner can change the plan or manage payment methods.
          </Alert>
        ) : null}

        <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          {/* Current plan card */}
          <div className="rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg sm:p-8">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-xs font-semibold text-primary">
                  Current plan
                </p>
                <h2 className="mt-2 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
                  {status.current_plan.name}
                </h2>
                <p className="mt-1 text-sm font-medium text-muted-foreground">
                  {status.subscription.status === "none"
                    ? "Free workspace"
                    : `Subscription ${status.subscription.status.replaceAll("_", " ")}`}
                </p>
              </div>
              {isOwner &&
              env.billingEnabled &&
              status.current_plan.key !== "free" &&
              !status.subscription.cancel_at_period_end ? (
                <form action={cancelSubscription}>
                  <Button type="submit" variant="secondary">
                    <XCircle className="size-4" aria-hidden="true" />
                    Cancel at period end
                  </Button>
                </form>
              ) : status.subscription.cancel_at_period_end ? (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-semibold text-warning shadow-neu-inset-sm">
                  Cancels at period end
                </span>
              ) : null}
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
          <div className="rounded-neu-lg bg-surface p-5 shadow-neu-raised sm:p-6">
            <div className="flex items-center gap-2">
              <Gauge className="size-5 text-primary" aria-hidden="true" />
              <h2 className="text-lg font-bold text-foreground">
                Monthly usage
              </h2>
            </div>
            <div className="mt-5 flex items-end justify-between gap-4">
              <p className="text-4xl font-bold tabular-nums text-foreground">
                {status.usage.renders_used}
              </p>
              <p className="pb-1 text-sm font-semibold text-muted-foreground">
                {meter.metered
                  ? `of ${status.usage.renders_limit} renders`
                  : "renders · unlimited"}
              </p>
            </div>
            {meter.metered && meter.ariaLabel ? (
              <div
                className="mt-4 h-2.5 overflow-hidden rounded-full bg-surface shadow-neu-inset-sm"
                role="progressbar"
                aria-label={meter.ariaLabel}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={meter.percent ?? 0}
              >
                <div
                  className="h-full rounded-full bg-primary"
                  style={{ width: `${meter.percent}%` }}
                />
              </div>
            ) : null}
            <p className="mt-3 text-sm font-medium text-muted-foreground">
              {meter.remainingLabel}
            </p>
          </div>
        </section>

        {/* Plan cards */}
        <section className="grid gap-4 lg:grid-cols-4">
          {status.plans.map((plan, index) => {
            const isCurrent = plan.key === status.current_plan.key;
            // The highest, non-current paid tier gets the recommended glow.
            const isRecommended =
              !isCurrent && index === status.plans.length - 1;
            return (
              <div
                key={plan.key}
                className={cn(
                  "flex flex-col rounded-neu-lg bg-surface p-5",
                  isRecommended ? "shadow-neu-glow" : "shadow-neu-raised",
                  isCurrent && "ring-1 ring-primary/40",
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="flex flex-wrap items-center gap-1.5 text-lg font-bold text-foreground">
                      {plan.name}
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2 py-0.5 text-[9px] font-semibold text-warning shadow-neu-inset-sm">
                        Demo
                      </span>
                    </h3>
                    <p className="mt-2 text-4xl font-bold tabular-nums text-foreground">
                      {formatPrice(plan.price_paise)}
                    </p>
                    <p className="text-sm font-medium text-muted-foreground">
                      per month
                    </p>
                  </div>
                  {isCurrent ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-surface px-3 py-1 text-xs font-semibold text-foreground shadow-neu-raised-sm">
                      Current
                    </span>
                  ) : null}
                </div>
                <ul className="mt-5 flex-1 space-y-2 text-sm font-medium text-foreground">
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
                {plan.key !== "free" &&
                !isCurrent &&
                isOwner &&
                env.billingEnabled ? (
                  <form action={startCheckout} className="mt-5">
                    <input type="hidden" name="plan" value={plan.key} />
                    <Button type="submit" className="w-full">
                      <ArrowUpRight className="size-4" aria-hidden="true" />
                      Upgrade
                    </Button>
                  </form>
                ) : plan.key !== "free" && !isCurrent ? (
                  <Button
                    type="button"
                    variant="secondary"
                    disabled
                    className="mt-5 w-full"
                  >
                    <ArrowUpRight className="size-4" aria-hidden="true" />
                    {isOwner ? "Billing unavailable" : "Owner only"}
                  </Button>
                ) : null}
              </div>
            );
          })}
        </section>

        {isOwner ? (
          <PaymentHistory receipts={receipts} demoMode={env.billingEnabled} />
        ) : null}

        <section className="rounded-neu-lg bg-surface p-5 shadow-neu-raised sm:p-6">
          <h2 className="text-lg font-bold text-foreground">Billing notes</h2>
          <div className="mt-3 grid gap-3 text-sm font-medium text-muted-foreground md:grid-cols-3">
            <p>
              {env.billingEnabled ? DEMO_NOTICE + "." : "Billing is disabled."}{" "}
              Payments run through a demo gateway in INR.
            </p>
            <p>
              Cancellations take effect at the end of the current period;
              exports stay available and only future premium actions are gated.
            </p>
            <p>
              A failed renewal marks the plan past due. Paid features stay on
              for a short grace period; after it, Free limits apply until the
              payment succeeds. Receipts appear in your payment history.
            </p>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-neu-md bg-surface p-3 shadow-neu-inset-sm">
      <p className="text-xs font-semibold text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-bold capitalize text-foreground">
        {value}
      </p>
    </div>
  );
}

function formatPrice(paise: number) {
  if (!paise) return "₹0";
  return `₹${Math.round(paise / 100)}`;
}
