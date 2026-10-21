import type { PlanDefinition, PublicPlansResponse } from "@motionly/shared";
import { env } from "@/lib/env";

export type PublicPlansResult =
  | { ok: true; plans: PlanDefinition[] }
  | { ok: false };

/**
 * Task 13 / B33: the pricing page reads its plans from the API public endpoint
 * (GET /plans), the single source of truth backed by PLAN_DEFINITIONS. If the
 * API is unreachable we return an explicit error state — never fabricated
 * prices — so the page can show an accessible error instead of wrong numbers.
 */
export async function getPublicPlans(): Promise<PublicPlansResult> {
  try {
    const response = await fetch(`${env.apiBaseUrl}/plans`, {
      // Plans change rarely; let the platform cache briefly but stay correct.
      cache: "no-store",
    });
    if (!response.ok) {
      return { ok: false };
    }
    const payload = (await response.json()) as PublicPlansResponse;
    if (!Array.isArray(payload.plans) || payload.plans.length === 0) {
      return { ok: false };
    }
    return { ok: true, plans: payload.plans };
  } catch {
    return { ok: false };
  }
}
