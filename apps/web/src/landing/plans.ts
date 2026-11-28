import { apiUrl } from "./site";

export type PublicPlan = {
  id: string;
  key: string;
  name: string;
  price_cents: number;
  currency: string;
  billing_cycle: string;
  token_allowance: number;
  limits: Record<string, number>;
  sort_order: number;
};

/** The public plans, straight from the API (never hard-coded here). Cached for five minutes; null when the API
 *  can't be reached, in which case the page shows no prices at all. */
export async function fetchPlans(): Promise<PublicPlan[] | null> {
  try {
    const r = await fetch(`${apiUrl()}/api/plans`, { next: { revalidate: 300 }, signal: AbortSignal.timeout(3000) });
    if (!r.ok) return null;
    const list = (await r.json()) as PublicPlan[];
    return Array.isArray(list) && list.length ? list : null;
  } catch {
    return null;
  }
}
