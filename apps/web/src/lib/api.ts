"use client";

import { msg, t } from "@/i18n";
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
    throw new ApiError(0, { code: "network", message: t("Can't reach Verity. Check your connection and try again.") });
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const json = text ? safeJson(text) : null;
  if (!res.ok) {
    const detail = json && typeof json === "object" && "detail" in json ? (json as { detail: unknown }).detail : null;
    if (detail && typeof detail === "object") throw new ApiError(res.status, detail as Record<string, unknown>);
    throw new ApiError(res.status, { code: "error", message: res.status >= 500 ? t("Verity had a problem. Try again in a moment.") : t("Request failed.") });
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

async function upload<T>(path: string, file: File, field = "file"): Promise<T> {
  const form = new FormData();
  form.append(field, file);
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { method: "POST", credentials: "same-origin", body: form, cache: "no-store" });
  } catch {
    throw new ApiError(0, { code: "network", message: t("Can't reach Verity. Check your connection and try again.") });
  }
  const json = safeJson(await res.text());
  if (!res.ok) {
    const detail = json && typeof json === "object" && "detail" in json ? (json as { detail: unknown }).detail : null;
    if (detail && typeof detail === "object") throw new ApiError(res.status, detail as Record<string, unknown>);
    throw new ApiError(res.status, { code: "error", message: res.status === 413 ? t("This file is too large.") : t("Upload failed.") });
  }
  return json as T;
}

/**
 * POSTs JSON and reads a server-sent event stream, calling onEvent for each event as it arrives.
 * Errors before the stream starts are thrown as ApiError, like every other call.
 */
export async function streamEvents(
  url: string,
  body: unknown,
  onEvent: (event: string, data: unknown) => void,
  headers: Record<string, string> = {},
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...headers },
      body: JSON.stringify(body),
      cache: "no-store",
      signal,
    });
  } catch (e) {
    if ((e as Error)?.name === "AbortError") return;
    throw new ApiError(0, { code: "network", message: t("Can't reach Verity. Check your connection and try again.") });
  }
  if (!res.ok || !res.body) {
    const json = safeJson(await res.text());
    const detail = json && typeof json === "object" && "detail" in json ? (json as { detail: unknown }).detail : null;
    throw new ApiError(res.status, (detail && typeof detail === "object" ? detail : { message: t("Request failed.") }) as Record<string, unknown>);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "message";
      let data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      onEvent(event, safeJson(data));
    }
  }
}

export const api = {
  upload,
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

/** The API's message in the user's language (its messages are part of the dictionary), else as sent. */
export function errorText(e: unknown): string {
  return e instanceof ApiError ? t(e.message) : t("Something went wrong.");
}

// Messages the API builds from parts, kept here so they're translated too.
export const API_MESSAGES = [
  msg("You don't have permission to do that."),
  msg("Enter a valid email address."),
  msg("Too many attempts. Try again shortly."),
  msg("Not found."),
];

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
  is_owner: boolean;
  deletion_requested_at: string | null;
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
      package_remaining: number;
      package_next_expiry: string | null;
      ai_available: boolean;
    };

// ---- Knowledge base ------------------------------------------------------------------------------

export type TraceStep = { name: string; detail?: string; ms?: number | null; state?: "done" | "running" | "pending" | "error"; kind?: "tool" };

export type Source = {
  id: string;
  kind: "file" | "website";
  name: string;
  status: "queued" | "processing" | "ready" | "failed";
  error_code: string | null;
  error_message: string | null;
  notice: string | null;
  chunk_count: number;
  token_count: number;
  steps: TraceStep[];
  created_at: string;
  updated_at: string;
  processed_at: string | null;
  file_type?: string;
  size_bytes?: number;
  root_url?: string;
  domain?: string;
  include_paths?: string[];
  exclude_paths?: string[];
  max_depth?: number;
  sync_interval?: "off" | "daily" | "weekly";
  last_synced_at?: string | null;
  next_sync_at?: string | null;
  page_count?: number;
};

export type KnowledgeStats = {
  usage: { documents: number; domains: number; crawled_pages: number; storage_bytes: number };
  limits: { documents: number; domains: number; crawled_pages: number; storage_mb: number };
  by_status: Record<string, number>;
  chunks: number;
  max_upload_mb: number;
};

export type Citation = {
  n: number;
  chunk_id?: string;
  source_id?: string;
  title: string;
  type: string;
  url: string | null;
  page: number | null;
  section: string;
  excerpt?: string;
  cited_text: string;
  score?: number;
};

export type ChatMessage = {
  id: string;
  role: "visitor" | "member" | "assistant" | "agent" | "system";
  kind: "text" | "form" | "event";
  content: string;
  data: {
    blocks?: { text: string; cites: number[] }[];
    outcome?: "answered" | "no_answer" | "fallback";
    sources?: { chunk_id: string; title: string; type: string; section: string; score: number; below_cutoff: boolean }[];
    offer_escalation?: boolean;
    event?: "escalated" | "agent_joined" | "resolved" | "ticket_created";
    reason?: string;
    name?: string;
    /** kind "form": the form block offered in the chat. */
    form?: FormBlock;
    state?: "offered" | "submitted" | "dismissed";
    ticket_number?: number;
    notice?: "ticket_created" | "ticket_created_auto";
    email?: string;
    custom?: boolean;
    values?: Record<string, FormValue>;
    /** "email": a visitor's reply that arrived by email. */
    channel?: "email";
    /** An agent reply that was also sent to the visitor by email. */
    emailed_to?: string;
  };
  citations: Citation[];
  confidence?: "high" | "medium" | "low" | "none" | null;
  is_fallback?: boolean;
  model?: string | null;
  latency_ms?: number | null;
  trace?: TraceStep[];
  author?: string | null;
  created_at: string;
};

export type Conversation = {
  id: string;
  chatbot_id: string | null;
  channel: "widget" | "dashboard";
  status: "open" | "escalated" | "resolved";
  title: string;
  visitor_name: string | null;
  visitor_email: string | null;
  origin: string | null;
  page_url: string | null;
  escalated_at: string | null;
  escalation_reason: string | null;
  resolved_at: string | null;
  message_count: number;
  last_message_at: string | null;
  summary?: string | null;
  summary_at?: string | null;
  created_at: string;
  confidence?: string | null;
  ticket_number?: number | null;
};

export type Chatbot = {
  id: string;
  name: string;
  public_key: string;
  allowed_domains: string[];
  appearance: { primary_color: string; position: "left" | "right"; launcher_label: string; theme: "light" | "dark" | "auto" };
  logo_url: string | null;
  language: string;
  tone: "friendly" | "professional" | "concise";
  welcome_message: string;
  instructions: string;
  fallback_message: string;
  no_answer_message: string;
  model: string;
  escalation: { enabled: boolean; ask_email: boolean; ticket_fallback?: boolean; wait_seconds?: number; fallback_form_id?: string | null };
  status: "active" | "paused";
  snippet?: string;
  created_at: string;
  updated_at: string;
};

// ---- Phase 3: forms and tickets -----------------------------------------------------------------

export type FieldType = "text" | "email" | "number" | "select" | "multiselect" | "date" | "textarea" | "checkbox";
export type FormValue = string | number | boolean | string[] | null;

export type FormField = {
  key: string;
  label: string;
  type: FieldType;
  required: boolean;
  help?: string;
  placeholder?: string;
  options: string[];
  validation: { min_length?: number; max_length?: number; pattern?: string; min?: number | string; max?: number | string; integer?: boolean; min_selected?: number; max_selected?: number };
};

export type FormBlock = { form_id: string; version: number; title: string; description: string; fields: FormField[]; submit_label: string };

export type ChatForm = {
  id: string;
  name: string;
  description: string;
  category_id: string;
  category: string | null;
  activation_condition: string;
  status: "draft" | "active" | "archived";
  fields: FormField[];
  version: number;
  submit_label: string;
  success_message: string;
  is_contact: boolean;
  chatbot_ids: string[];
  created_at: string;
  updated_at: string;
};

export type TicketCategory = { id: string; name: string; description: string; position: number; archived: boolean; open_tickets: number };

export type TicketStatus = "new" | "open" | "pending" | "resolved" | "closed";
export type TicketPriority = "low" | "normal" | "high" | "urgent";

export type Ticket = {
  id: string;
  number: number;
  subject: string;
  description: string;
  status: TicketStatus;
  priority: TicketPriority;
  category_id: string | null;
  category: string | null;
  assignee_id: string | null;
  assignee: string | null;
  team_id: string | null;
  team: string | null;
  assigned_at: string | null;
  first_response_at: string | null;
  source: "chatbot_form" | "conversation" | "manual" | "email";
  form_id: string | null;
  conversation_id: string | null;
  requester_name: string | null;
  requester_email: string | null;
  reopened_count: number;
  resolved_at: string | null;
  closed_at: string | null;
  last_activity_at: string | null;
  created_at: string;
};

export type TicketEvent = {
  id: string;
  kind: "created" | "customer_message" | "agent_reply" | "note" | "status" | "assignment" | "priority" | "category" | "reopened" | "routed" | "team";
  /** data.channel is "email" for messages that came or went by email; data.email_message_id links the email. */
  body: string;
  data: Record<string, string | number | null | undefined>;
  internal: boolean;
  author: string | null;
  created_at: string;
};

export type TicketDetail = {
  ticket: Ticket;
  form: { name: string | null; version: number | null; fields: { key: string; label: string; type: FieldType; value: FormValue }[] } | null;
  events: TicketEvent[];
  conversation: Conversation | null;
  messages: ChatMessage[];
  emails: TicketEmail[];
  /** Where a reply goes: the ticket's chat (also emailed when the visitor left an address) or email in the thread. */
  reply_channel: { kind: "chat"; email: string | null } | { kind: "email"; to: string; from: string | null } | null;
  can_manage: boolean;
  can_reply: boolean;
  summary: { text: string; at: string } | null;
  possible_spam: boolean;
};

// ---- Email (Phase 4) ---------------------------------------------------------------------------

export type MailSecurity = "ssl" | "starttls";

export type EmailAccount = {
  id: string;
  name: string;
  address: string;
  display_name: string;
  kind: "personal" | "organization";
  imap_host: string;
  imap_port: number;
  imap_security: MailSecurity;
  smtp_host: string;
  smtp_port: number;
  smtp_security: MailSecurity;
  username: string;
  has_password: boolean;
  status: "active" | "paused" | "error" | "disconnected";
  fetch_status: "idle" | "queued" | "fetching" | "ok" | "error";
  last_fetch_at: string | null;
  last_success_at: string | null;
  last_error_code: string | null;
  last_error: string | null;
  auto_reply: AutoReply | null;
  created_at: string;
};

export type EmailAccounts = { items: EmailAccount[]; limit: number; used: number; default_account_id: string | null };

export type AutoReply = { mode: "off" | "template" | "ai"; subject: string; body: string };

export type EmailSettings = {
  auto_reply: AutoReply;
  form_confirmation: boolean;
  default_account_id: string | null;
  spam_filter: boolean;
  allow_senders: string[];
  block_senders: string[];
  model: string;
  models: string[];
};

export type EmailAttachmentInfo = { id: string; filename: string; mime: string; size: number; available: boolean; blocked_reason: string | null };

export type HeldEmail = {
  id: string;
  status: "spam" | "promotion" | "rate_limited" | "ticketed" | "deleted" | string;
  from_addr: string;
  from_name: string;
  to: string[];
  subject: string;
  snippet: string;
  verdict: { rules?: string[]; decided_by?: "rules" | "ai"; ai_used?: boolean; confidence?: string; ai_note?: string; loop?: string[] };
  ticket_id: string | null;
  received_at: string | null;
  text?: string;
  html?: string | null;
  attachments?: EmailAttachmentInfo[];
};

export type TicketEmail = {
  id: string;
  direction: "in" | "out";
  status: string;
  kind: "auto_reply" | "agent_reply" | "chat_reply" | "form_confirmation" | null;
  from_addr: string;
  from_name: string;
  to: string[];
  cc: string[];
  subject: string;
  text: string;
  html: string | null;
  error: string | null;
  event_id: string | null;
  attachments: EmailAttachmentInfo[];
  via_platform: boolean;
  created_at: string;
  sent_at: string | null;
  received_at: string | null;
};

// ---- Phase 5A: API tools and routing ----------------------------------------------------------

export type ToolParamType = "string" | "integer" | "number" | "boolean";
export type ToolParam = {
  key: string;
  label: string;
  type: ToolParamType;
  description: string;
  required: boolean;
  location: "path" | "query" | "body";
  source: "ai" | "visitor_email" | "visitor_name";
  enum: (string | number)[];
  pattern: string | null;
  max_length: number | null;
};
export type ToolCallStatus = "ok" | "http_error" | "timeout" | "blocked" | "rate_limited" | "invalid_args" | "too_large" | "failed";
export type ApiTool = {
  id: string;
  name: string;
  display_name: string;
  description: string;
  method: "GET" | "POST";
  url_template: string;
  host: string | null;
  headers: Record<string, string>;
  parameters: ToolParam[];
  auth_type: "none" | "api_key" | "bearer";
  auth_header: string | null;
  has_secret: boolean;
  timeout_seconds: number;
  max_response_kb: number;
  rate_limit_per_minute: number;
  status: "draft" | "active" | "disabled";
  last_test_at: string | null;
  last_test_status: ToolCallStatus | null;
  last_test_word: string | null;
  chatbots: { id: string; name: string }[] | null;
  created_at: string;
};
export type ApiTools = { items: ApiTool[]; limit: number; used: number };
export type ToolTestResult = {
  status: ToolCallStatus;
  status_word: string;
  http_status: number | null;
  latency_ms: number;
  response_bytes: number;
  error_code: string | null;
  error: string | null;
  request: string;
  preview: string;
  truncated: boolean;
};
export type ToolCall = {
  id: string;
  origin: "chat" | "test";
  status: ToolCallStatus;
  status_word: string;
  http_status: number | null;
  latency_ms: number;
  response_bytes: number;
  error_code: string | null;
  args: Record<string, unknown>;
  conversation_id: string | null;
  created_at: string;
};
export type BotTool = { id: string; name: string; display_name: string; description: string; enabled: boolean };

export type Team = { id: string; name: string; description: string; category_ids: string[]; members: { id: string; name: string; email: string }[] };
export type RoutingRule = {
  id: string;
  name: string;
  position: number;
  enabled: boolean;
  conditions: { sources?: string[]; category_ids?: string[]; priorities?: string[]; keywords?: string[]; requester_domains?: string[] };
  action: { team_id?: string; assignee_id?: string };
};
export type RoutingSettings = { enabled: boolean; default_team_id: string | null };
export type MemberRouting = {
  user_id: string;
  name: string;
  email: string;
  availability: "available" | "away";
  max_open: number | null;
  skills: string[];
  open_tickets: number;
  teams: { id: string; name: string }[];
  last_assigned_at: string | null;
};
