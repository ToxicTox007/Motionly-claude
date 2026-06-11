import { LegalPage } from "@/components/legal-page";

export default function RefundPage() {
  return (
    <LegalPage
      title="Refund Policy"
      updated="May 31, 2026"
      sections={[
        {
          title: "Subscriptions",
          body: "Paid plans renew on the billing cadence shown at checkout. Canceling stops future renewals and keeps access through the current paid period.",
        },
        {
          title: "Refund review",
          body: "Refund requests are reviewed for duplicate charges, accidental upgrades, unresolved service outages, or billing errors. Approved refunds are returned to the original payment method.",
        },
        {
          title: "Usage-based limits",
          body: "Rendered exports, licensed media usage, and other consumed platform resources may affect refund eligibility.",
        },
      ]}
    />
  );
}
