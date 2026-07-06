export type ProductNotificationKind =
  | "blocker"
  | "persistent_status"
  | "short_action_feedback"
  | "destructive_confirmation"
  | "durable_state";

export type ProductNotificationChannel =
  | "inline_alert"
  | "inline_status"
  | "toast"
  | "modal"
  | "badge";

export const PRODUCT_NOTIFICATION_TAXONOMY: Record<
  ProductNotificationKind,
  ProductNotificationChannel
> = {
  blocker: "inline_alert",
  persistent_status: "inline_status",
  short_action_feedback: "toast",
  destructive_confirmation: "modal",
  durable_state: "badge",
};

export function shouldToast(kind: ProductNotificationKind) {
  return PRODUCT_NOTIFICATION_TAXONOMY[kind] === "toast";
}
