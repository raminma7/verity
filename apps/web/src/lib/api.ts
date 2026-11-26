"use client";

export class ApiError extends Error {
  status: number;
  code: string;
  field?: string;
  data: Record<string, unknown>;

  constructor(status: number, detail: { code?: string; message?: string; field?: string } & Record<string, unknown>) {
    super(detail.message ?? "Something went wrong.");
    this.status = status;
    this.code = detail.code ?? "error";
    this.field = detail.field;
    this.data = detail;
  }
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, { code: "network", message: "Can't reach Verity. Check your connection and try again." });
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const json = text ? safeJson(text) : null;
  if (!res.ok) {
    const detail = json && typeof json === "object" && "detail" in json ? (json as { detail: unknown }).detail : null;
    if (detail && typeof detail === "object") throw new ApiError(res.status, detail as Record<string, unknown>);
    throw new ApiError(res.status, { code: "error", message: res.status >= 500 ? "Verity had a problem. Try again in a moment." : "Request failed." });
  }
  return json as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong.";
}

// ---- Types shared with the API -------------------------------------------------------------------

export type User = {
  id: string;
  full_name: string;
  email: string;
  email_verified: boolean;
  is_system_admin: boolean;
  locale: string;
  timezone: string;
  notification_prefs: Record<string, boolean>;
  totp_enabled: boolean;
  last_workspace_id: string | null;
  created_at: string;
};

export type WorkspaceSummary = {
  id: string;
  name: string;
  slug: string;
  status: string;
  role: { id: string; key: string; name: string };
  permissions: string[];
  subscribed: boolean;
  plan_name: string | null;
};

export type Role = { id: string; key: string; name: string; description: string; permissions: string[]; is_system: boolean; rank: number };

export type Limits = Record<"documents" | "domains" | "crawled_pages" | "chatbot_domains" | "email_accounts" | "members" | "storage_mb", number>;

export type WorkspaceDetail = {
  workspace: { id: string; name: string; slug: string; status: string; settings: Record<string, unknown>; created_at: string };
  role: Role;
  permissions: string[];
  subscription: null | {
    status: string;
    active: boolean;
    plan_id: string;
    plan_name: string;
    renews_at: string;
    cancel_at_period_end: boolean;
  };
  limits: Limits;
  usage: { seats: number };
  ai_available: boolean;
};

export type Plan = {
  id: string;
  key: string;
  name: string;
  description: string;
  price_cents: number;
  currency: string;
  billing_cycle: "month" | "year";
  token_allowance: number;
  limits: Limits;
  features: string[];
  is_public: boolean;
  is_active: boolean;
  sort_order: number;
};

export type UsageSummary =
  | { active: false }
  | {
      active: true;
      period_start: string;
      period_end: string;
      allowance: number;
      bonus: number;
      total_allowance: number;
      consumed_weighted: number;
      reserved: number;
      remaining: number;
      percent_used: number;
      actual_input_tokens: number;
      actual_output_tokens: number;
      embedding_weighted: number;
      ai_available: boolean;
    };
