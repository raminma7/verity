"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { CallStatusBadge, LOCATION_WORDS, placeholders, SOURCE_WORDS, ToolStatusBadge } from "@/components/tools";
import { Badge, Banner, Button, Card, dateTime, Field, PageHeader, Segmented, Select, Spinner, Switch } from "@/components/ui";
import { api, errorText, type ApiTool, type ToolCall, type ToolParam, type ToolTestResult } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Draft = {
  name: string;
  display_name: string;
  description: string;
  method: "GET" | "POST";
  url_template: string;
  headers: [string, string][];
  parameters: ToolParam[];
  auth_type: "none" | "api_key" | "bearer";
  auth_header: string;
  timeout_seconds: number;
  max_response_kb: number;
  rate_limit_per_minute: number;
};

const NEW: Draft = {
  name: "order_status",
  display_name: "Order status",
  description: "Look up the shipping status of a customer's order by its order number.",
  method: "GET",
  url_template: "https://",
  headers: [],
  parameters: [],
  auth_type: "bearer",
  auth_header: "X-Api-Key",
  timeout_seconds: 8,
  max_response_kb: 32,
  rate_limit_per_minute: 60,
};

const emptyParam = (key = ""): ToolParam => ({
  key,
  label: "",
  type: "string",
  description: "",
  required: true,
  location: "query",
  source: "ai",
  enum: [],
  pattern: null,
  max_length: null,
});

function fromTool(t: ApiTool): Draft {
  return {
    name: t.name,
    display_name: t.display_name,
    description: t.description,
    method: t.method,
    url_template: t.url_template,
    headers: Object.entries(t.headers),
    parameters: t.parameters,
    auth_type: t.auth_type,
    auth_header: t.auth_header ?? "X-Api-Key",
    timeout_seconds: t.timeout_seconds,
    max_response_kb: t.max_response_kb,
    rate_limit_per_minute: t.rate_limit_per_minute,
  };
}

export default function ToolEditor() {
  const { id } = useParams<{ id: string }>();
  const isNew = id === "new";
  const router = useRouter();
  const { can } = useSession();
  const manage = can("integrations.manage");
  const tool = useData<ApiTool>(manage && !isNew ? `/tools/${id}` : null);
  const calls = useData<{ items: ToolCall[] }>(manage && !isNew ? `/tools/${id}/calls` : null);
  const [d, setD] = useState<Draft>(NEW);
  const [secret, setSecret] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const [sample, setSample] = useState<Record<string, string>>({});
  const [sampleEmail, setSampleEmail] = useState("");
  const [result, setResult] = useState<ToolTestResult | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    if (tool.data) setD(fromTool(tool.data));
  }, [tool.data]);

  const holes = useMemo(() => placeholders(d.url_template), [d.url_template]);
  const pathKeys = d.parameters.filter((p) => p.location === "path").map((p) => p.key);
  const missing = holes.filter((h) => !pathKeys.includes(h));
  const extra = pathKeys.filter((k) => !holes.includes(k));

  if (!manage) return <Banner tone="neutral">Your role can’t manage integrations.</Banner>;
  if (!isNew && tool.loading && !tool.data) return <Spinner />;
  if (!isNew && (tool.error || !tool.data)) return <Banner tone="danger">{tool.error?.message ?? "Couldn’t load this tool."}</Banner>;
  const t = tool.data;

  function body() {
    return {
      name: d.name.trim(),
      display_name: d.display_name.trim(),
      description: d.description.trim(),
      method: d.method,
      url_template: d.url_template.trim(),
      headers: Object.fromEntries(d.headers.filter(([k]) => k.trim())),
      parameters: d.parameters.map((p) => ({ ...p, label: p.label || p.key, pattern: p.pattern || null, max_length: p.max_length || null })),
      auth_type: d.auth_type,
      auth_header: d.auth_type === "api_key" ? d.auth_header.trim() : null,
      timeout_seconds: d.timeout_seconds,
      max_response_kb: d.max_response_kb,
      rate_limit_per_minute: d.rate_limit_per_minute,
    };
  }

  async function run(key: string, fn: () => Promise<void>, ok?: string) {
    setBusy(key);
    setMsg(null);
    try {
      await fn();
      if (ok) setMsg({ tone: "brand", text: ok });
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }

  const save = () =>
    run(
      "save",
      async () => {
        if (isNew) {
          const created = await api.post<ApiTool>("/tools", { ...body(), secret: d.auth_type === "none" ? null : secret || null });
          setSecret("");
          router.replace(`/tools/${created.id}`);
          return;
        }
        await api.patch(`/tools/${id}`, body());
        if (secret && d.auth_type !== "none") {
          await api.put(`/tools/${id}/secret`, { secret });
          setSecret("");
          setReplacing(false);
        }
        await tool.reload();
      },
      isNew ? undefined : "Saved.",
    );

  const setStatus = (status: ApiTool["status"]) =>
    run(
      "status",
      async () => {
        await api.patch(`/tools/${id}`, { status });
        await tool.reload();
      },
      status === "active" ? "The tool is on. Link it to a chatbot under Chatbots → API tools." : "The tool is off and no chatbot offers it.",
    );

  const test = () =>
    run("test", async () => {
      setResult(null);
      const args: Record<string, unknown> = {};
      for (const p of d.parameters.filter((x) => x.source === "ai")) {
        const v = sample[p.key];
        if (v === undefined || v === "") continue;
        args[p.key] = p.type === "integer" ? parseInt(v, 10) : p.type === "number" ? Number(v) : p.type === "boolean" ? v === "true" : v;
      }
      setResult(await api.post<ToolTestResult>(`/tools/${id}/test`, { args, visitor_email: sampleEmail || null, visitor_name: sampleEmail ? "Test visitor" : null }));
      await Promise.all([tool.reload(), calls.reload()]);
    });

  const setP = (i: number, patch: Partial<ToolParam>) => setD({ ...d, parameters: d.parameters.map((p, j) => (j === i ? { ...p, ...patch } : p)) });
  const needsEmail = d.parameters.some((p) => p.source !== "ai");

  return (
    <>
      <PageHeader
        eyebrow="API tools"
        title={isNew ? "New API tool" : t?.display_name ?? ""}
        subtitle={isNew ? "Describe the request once. The chatbot fills in the values it learns in the chat; Verity adds the key and makes the call." : t?.url_template}
        actions={
          t ? (
            <span className="vr-actions">
              <ToolStatusBadge status={t.status} />
              {t.status === "active" ? (
                <Button variant="ghost" busy={busy === "status"} onClick={() => setStatus("disabled")}>
                  Turn off
                </Button>
              ) : (
                <Button variant="primary" busy={busy === "status"} onClick={() => setStatus("active")}>
                  Turn on
                </Button>
              )}
            </span>
          ) : null
        }
      />
      <p className="vr-help">
        <Link href="/tools">← All API tools</Link>
      </p>
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      <div className="vr-tool-edit">
        <div className="vr-stack">
          <Card title="What the tool does">
            <div className="vr-form">
              <div className="vr-grid2">
                <Field label="Name" value={d.display_name} onChange={(e) => setD({ ...d, display_name: e.target.value })} maxLength={80} />
                <Field
                  label="Tool name for the AI"
                  value={d.name}
                  onChange={(e) => setD({ ...d, name: e.target.value })}
                  help="Lowercase letters, digits and _, like order_status."
                  maxLength={64}
                  className="vr-mono-input"
                />
              </div>
              <label className="vr-field">
                <span className="vr-field-top">When the AI should use it</span>
                <span className="vr-input vr-textarea">
                  <textarea rows={3} value={d.description} maxLength={1500} onChange={(e) => setD({ ...d, description: e.target.value })} />
                </span>
                <span className="vr-help">Plain words: what it looks up and which value it needs. The AI reads this to decide.</span>
              </label>
            </div>
          </Card>

          <Card title="Request">
            <div className="vr-form">
              <div className="vr-tool-url-row">
                <Segmented
                  label="Method"
                  value={d.method}
                  onChange={(v) => setD({ ...d, method: v as Draft["method"] })}
                  options={[
                    { value: "GET", label: "GET" },
                    { value: "POST", label: "POST" },
                  ]}
                />
                <Field
                  label="Address"
                  value={d.url_template}
                  onChange={(e) => setD({ ...d, url_template: e.target.value })}
                  placeholder="https://shop.example.com/orders/{order_id}"
                  className="vr-grow"
                  help="https only. Put {placeholders} in the path for values from the chat; the host never changes."
                />
              </div>
              {missing.length || extra.length ? (
                <Banner tone="warn">
                  {missing.length ? `Add a path parameter for ${missing.map((m) => `{${m}}`).join(", ")}. ` : ""}
                  {extra.length ? `${extra.join(", ")} ${extra.length > 1 ? "aren’t" : "isn’t"} in the address.` : ""}
                  {missing.length ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setD({ ...d, parameters: [...d.parameters, ...missing.map((m) => ({ ...emptyParam(m), location: "path" as const }))] })}
                    >
                      Add them
                    </Button>
                  ) : null}
                </Banner>
              ) : holes.length ? (
                <span className="vr-help">
                  <Badge tone="brand" dot>
                    Placeholders match
                  </Badge>{" "}
                  {holes.map((h) => `{${h}}`).join(", ")}
                </span>
              ) : null}

              <div className="vr-field">
                <span className="vr-field-top">Parameters</span>
                {d.parameters.length ? (
                  <div className="vr-params" aria-label="Parameters">
                    {d.parameters.map((p, i) => (
                      <div key={i} className="vr-param">
                        <div className="vr-grid3">
                          <Field label="Key" value={p.key} onChange={(e) => setP(i, { key: e.target.value })} className="vr-mono-input" maxLength={40} />
                          <Select
                            label="Type"
                            value={p.type}
                            onChange={(v) => setP(i, { type: v as ToolParam["type"] })}
                            options={[
                              { value: "string", label: "Text" },
                              { value: "integer", label: "Whole number" },
                              { value: "number", label: "Number" },
                              { value: "boolean", label: "Yes / no" },
                            ]}
                          />
                          <Select
                            label="Sent"
                            value={p.location}
                            onChange={(v) => setP(i, { location: v as ToolParam["location"] })}
                            options={Object.entries(LOCATION_WORDS).map(([value, label]) => ({ value, label }))}
                          />
                        </div>
                        <div className="vr-grid3">
                          <Field label="What it is" value={p.description} onChange={(e) => setP(i, { description: e.target.value })} placeholder="The order number" maxLength={300} />
                          <Select
                            label="Value comes from"
                            value={p.source}
                            onChange={(v) => setP(i, { source: v as ToolParam["source"], type: v === "ai" ? p.type : "string" })}
                            options={Object.entries(SOURCE_WORDS).map(([value, label]) => ({ value, label }))}
                          />
                          <Field label="Pattern (optional)" value={p.pattern ?? ""} onChange={(e) => setP(i, { pattern: e.target.value || null })} placeholder="[0-9]{5,8}" className="vr-mono-input" />
                        </div>
                        <div className="vr-actions">
                          <Switch label="Required" checked={p.required || p.location === "path"} disabled={p.location === "path"} onChange={(on) => setP(i, { required: on })} />
                          <Button size="sm" variant="ghost" onClick={() => setD({ ...d, parameters: d.parameters.filter((_, j) => j !== i) })}>
                            Remove
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <span className="vr-help">No parameters yet.</span>
                )}
                <div>
                  <Button size="sm" icon="plus" onClick={() => setD({ ...d, parameters: [...d.parameters, emptyParam()] })}>
                    Add parameter
                  </Button>
                </div>
                <span className="vr-help">
                  Values from the visitor’s email or name are filled in by Verity, never by the AI, so your API can check that an order belongs to the person asking.
                </span>
              </div>

              <div className="vr-field">
                <span className="vr-field-top">Extra headers (optional)</span>
                {d.headers.map(([k, v], i) => (
                  <div key={i} className="vr-actions">
                    <Field label="Header" value={k} onChange={(e) => setD({ ...d, headers: d.headers.map((h, j) => (j === i ? [e.target.value, h[1]] : h)) })} />
                    <Field label="Value" value={v} onChange={(e) => setD({ ...d, headers: d.headers.map((h, j) => (j === i ? [h[0], e.target.value] : h)) })} help="Not for secrets." />
                    <Button size="sm" variant="ghost" onClick={() => setD({ ...d, headers: d.headers.filter((_, j) => j !== i) })}>
                      Remove
                    </Button>
                  </div>
                ))}
                <div>
                  <Button size="sm" variant="ghost" icon="plus" onClick={() => setD({ ...d, headers: [...d.headers, ["", ""]] })}>
                    Add header
                  </Button>
                </div>
              </div>
            </div>
          </Card>

          <Card title="Authentication">
            <div className="vr-form">
              <Segmented
                label="Authentication"
                value={d.auth_type}
                onChange={(v) => setD({ ...d, auth_type: v as Draft["auth_type"] })}
                options={[
                  { value: "bearer", label: "Bearer token" },
                  { value: "api_key", label: "API key header" },
                  { value: "none", label: "None" },
                ]}
              />
              {d.auth_type === "api_key" ? (
                <Field label="Header name" value={d.auth_header} onChange={(e) => setD({ ...d, auth_header: e.target.value })} placeholder="X-Api-Key" />
              ) : null}
              {d.auth_type !== "none" ? (
                t?.has_secret && !replacing ? (
                  <div className="vr-actions">
                    <Badge tone="brand" dot>
                      Saved · encrypted
                    </Badge>
                    <span className="vr-help">The {d.auth_type === "bearer" ? "token" : "key"} can’t be shown again.</span>
                    <Button size="sm" variant="ghost" onClick={() => setReplacing(true)}>
                      Replace
                    </Button>
                  </div>
                ) : (
                  <Field
                    label={d.auth_type === "bearer" ? "Token" : "Key"}
                    type="password"
                    autoComplete="new-password"
                    value={secret}
                    onChange={(e) => setSecret(e.target.value)}
                    help={d.auth_type === "bearer" ? "Sent as Authorization: Bearer …" : `Sent in the ${d.auth_header || "key"} header.`}
                  />
                )
              ) : (
                <span className="vr-help">The API is called without credentials.</span>
              )}
            </div>
          </Card>

          <Card title="Limits">
            <div className="vr-grid3">
              <Field label="Timeout (seconds)" type="number" min={1} max={15} value={String(d.timeout_seconds)} onChange={(e) => setD({ ...d, timeout_seconds: Number(e.target.value) })} />
              <Field label="Largest response (KB)" type="number" min={1} max={256} value={String(d.max_response_kb)} onChange={(e) => setD({ ...d, max_response_kb: Number(e.target.value) })} />
              <Field label="Calls per minute" type="number" min={1} max={600} value={String(d.rate_limit_per_minute)} onChange={(e) => setD({ ...d, rate_limit_per_minute: Number(e.target.value) })} />
            </div>
          </Card>

          <div className="vr-actions">
            <Button variant="primary" busy={busy === "save"} onClick={save}>
              {isNew ? "Create tool" : "Save changes"}
            </Button>
            {isNew ? <span className="vr-help">New tools start as drafts: test one, then turn it on.</span> : null}
            {!isNew ? (
              confirmDelete ? (
                <>
                  <Button
                    variant="danger"
                    busy={busy === "delete"}
                    onClick={() =>
                      run("delete", async () => {
                        await api.del(`/tools/${id}`);
                        router.replace("/tools");
                      })
                    }
                  >
                    Delete and erase the key
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
                    Keep
                  </Button>
                </>
              ) : (
                <Button variant="ghost" onClick={() => setConfirmDelete(true)}>
                  Delete
                </Button>
              )
            ) : null}
          </div>
        </div>

        <div className="vr-stack">
          {!isNew && t ? (
            <Card title="Test this tool">
              <div className="vr-form" aria-label="Test this tool">
                <span className="vr-help">Runs the real call with these values. Uses no AI tokens.</span>
                {t.parameters
                  .filter((p) => p.source === "ai")
                  .map((p) => (
                    <Field key={p.key} label={p.label || p.key} value={sample[p.key] ?? ""} onChange={(e) => setSample({ ...sample, [p.key]: e.target.value })} className="vr-mono-input" />
                  ))}
                {needsEmail ? <Field label="Visitor’s email" type="email" value={sampleEmail} onChange={(e) => setSampleEmail(e.target.value)} /> : null}
                <div>
                  <Button icon="send" busy={busy === "test"} onClick={test}>
                    Run test
                  </Button>
                </div>
                {result ? (
                  <div className="vr-tool-result" aria-label="Test result">
                    <div className="vr-actions">
                      <CallStatusBadge status={result.status} http={result.http_status} />
                      <span className="vr-num">{result.latency_ms} ms</span>
                      {result.response_bytes ? <span className="vr-num">{result.response_bytes.toLocaleString()} B</span> : null}
                    </div>
                    <span className="vr-mono vr-help">{result.request}</span>
                    {result.error ? <span className="vr-help vr-help-error">{result.error}</span> : null}
                    {result.preview ? <pre className="vr-tool-preview">{result.preview}</pre> : null}
                  </div>
                ) : null}
              </div>
            </Card>
          ) : null}
          {!isNew && t ? (
            <Card title="Chatbots using it">
              {t.chatbots?.length ? (
                <ul className="vr-bullets">
                  {t.chatbots.map((b) => (
                    <li key={b.id}>
                      <Link href={`/chatbots/${b.id}`}>{b.name}</Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="vr-help">{t.status === "active" ? "Link it under Chatbots → a chatbot → API tools." : "Turn the tool on, then link it to a chatbot."}</span>
              )}
            </Card>
          ) : null}
          {!isNew ? (
            <Card title="Recent calls" padded={false}>
              {calls.data?.items.length ? (
                <table className="vr-table">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>From</th>
                      <th>Result</th>
                      <th>Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {calls.data.items.slice(0, 20).map((c) => (
                      <tr key={c.id}>
                        <td className="vr-sub">{dateTime(c.created_at)}</td>
                        <td>{c.origin === "test" ? "Test" : c.conversation_id ? <Link href={`/conversations/${c.conversation_id}`}>Chat</Link> : "Chat"}</td>
                        <td>
                          <CallStatusBadge status={c.status} http={c.http_status} />
                        </td>
                        <td className="vr-num">{c.latency_ms} ms</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="vr-panel-body">
                  <span className="vr-help">No calls yet.</span>
                </div>
              )}
            </Card>
          ) : null}
        </div>
      </div>
    </>
  );
}
