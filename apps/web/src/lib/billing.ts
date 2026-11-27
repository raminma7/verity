/** The billing overview (GET /billing), shared by the billing pages. */

export type Payment = {
  id: string;
  kind: "subscription" | "upgrade" | "renewal" | "package";
  description: string;
  number: string | null;
  amount_cents: number;
  subtotal_cents: number;
  tax_cents: number;
  refunded_cents: number;
  currency: string;
  status: "succeeded" | "failed" | "refunded" | "void";
  failure_message: string | null;
  attempt_count: number;
  next_retry_at: string | null;
  hosted_url: string | null;
  pdf_url: string | null;
  period_start: string | null;
  period_end: string | null;
  created_at: string;
};

export type Billing = {
  provider: "mock" | "stripe";
  subscription: null | {
    active: boolean;
    status: "active" | "past_due" | "canceled";
    provider: string;
    plan_id: string;
    terms: { plan_name: string; price_cents: number; currency: string; billing_cycle: string; token_allowance: number };
    current_period_start: string;
    current_period_end: string;
    cancel_at_period_end: boolean;
    pending_plan: { id: string; name: string } | null;
    past_due_since: string | null;
    next_retry_at: string | null;
    grace_ends_at: string | null;
  };
  account: { currency: string | null; has_portal: boolean; test_card: "ok" | "declines" | null };
  payments: Payment[];
  can_manage: boolean;
};
