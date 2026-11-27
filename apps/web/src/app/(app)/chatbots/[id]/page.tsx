"use client";

import { t as tr, msg } from "@/i18n";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge, Banner, Button, Card, Field, PageHeader, Segmented, Select, Spinner, Switch } from "@/components/ui";
import { api, errorText, type BotTool, type Chatbot, type ChatForm } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

const LANGS = [
  ["auto", "Same as the customer"],
  ["en", "English"],
  ["de", "German"],
  ["fr", "French"],
  ["es", "Spanish"],
  ["it", "Italian"],
  ["nl", "Dutch"],
  ["pt", "Portuguese"],
  ["sv", "Swedish"],
  ["pl", "Polish"],
  ["tr", "Turkish"],
  ["fa", "Persian"],
  ["ar", "Arabic"],
  ["ja", "Japanese"],
  ["zh", "Chinese"],
] as const;

const MODELS: Record<string, string> = {
  "claude-opus-5-5": msg("Claude Opus 5.5 · most capable"),
  "claude-sonnet-5-5": msg("Claude Sonnet 5.5 · balanced"),
  "claude-haiku-5-5": msg("Claude Haiku 5.5 · fastest, lowest cost"),
};

export default function ChatbotSettings() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { can } = useSession();
  const data = useData<Chatbot>(`/chatbots/${id}`);
  const forms = useData<ChatForm[]>("/forms");
  const [bot, setBot] = useState<Chatbot | null>(null);
  const [tab, setTab] = useState("behaviour");
  const [domains, setDomains] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const manage = can("chatbots.manage");

  useEffect(() => {
    if (data.data) {
      setBot(data.data);
      setDomains(data.data.allowed_domains.join("\n"));
    }
  }, [data.data]);

  if (!bot) return data.error ? <Banner tone="danger">{data.error.message}</Banner> : <Spinner />;
  const set = <K extends keyof Chatbot>(k: K, v: Chatbot[K]) => setBot({ ...bot, [k]: v });

  async function save(patch: Partial<Chatbot>, ok = "Saved.") {
    setBusy(true);
    setMsg(null);
    try {
      const b = await api.patch<Chatbot>(`/chatbots/${id}`, patch);
      data.setData(b);
      setMsg({ tone: "brand", text: ok });
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  }

  const saveAll = () =>
    save({
      name: bot.name,
      appearance: bot.appearance,
      language: bot.language,
      tone: bot.tone,
      welcome_message: bot.welcome_message,
      instructions: bot.instructions,
      fallback_message: bot.fallback_message,
      no_answer_message: bot.no_answer_message,
      model: bot.model,
      escalation: bot.escalation,
    });

  return (
    <>
      <div>
        <Link className="vr-link" href="/chatbots" style={{ fontSize: 13 }}>
          {tr("← Chatbots")}
        </Link>
      </div>
      <PageHeader
        title={bot.name}
        subtitle={
          <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
            <Badge tone={bot.status === "active" && bot.allowed_domains.length ? "brand" : "neutral"} dot>
              {bot.status === "paused" ? tr("Paused") : bot.allowed_domains.length ? tr("Live") : tr("Not installed")}
            </Badge>
            <Link className="vr-link" href={`/conversations?chatbot=${bot.id}`}>
              {tr("Conversations →")}
            </Link>
          </span>
        }
        actions={
          manage ? (
            <>
              <Button onClick={() => save({ status: bot.status === "active" ? "paused" : "active" }, bot.status === "active" ? "Chatbot paused." : "Chatbot is live.")}>
                {bot.status === "active" ? tr("Pause") : tr("Resume")}
              </Button>
              <Button variant="primary" busy={busy} onClick={saveAll}>
                {tr("Save changes")}
              </Button>
            </>
          ) : null
        }
      />
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      <Segmented
        label={tr("Settings")}
        value={tab}
        onChange={setTab}
        options={[
          { value: "behaviour", label: tr("Behaviour") },
          { value: "appearance", label: tr("Appearance") },
          { value: "install", label: tr("Domains & install") },
          { value: "handoff", label: tr("Handoff") },
          { value: "tools", label: tr("API tools") },
        ]}
      />
      <div className="vr-bot-layout">
        <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
          {tab === "behaviour" ? (
            <Card title={tr("How it answers")}>
              <div className="vr-form">
                <Field label={tr("Name")} value={bot.name} onChange={(e) => set("name", e.target.value)} disabled={!manage} />
                <div className="vr-form-row" style={{ gridTemplateColumns: "1fr 1fr" }}>
                  <Select label={tr("Language")} value={bot.language} onChange={(v) => set("language", v)} options={LANGS.map(([value, label]) => ({ value, label }))} disabled={!manage} />
                  <Select
                    label={tr("Tone")}
                    value={bot.tone}
                    onChange={(v) => set("tone", v as Chatbot["tone"])}
                    options={[
                      { value: "friendly", label: tr("Friendly") },
                      { value: "professional", label: tr("Professional") },
                      { value: "concise", label: tr("Concise") },
                    ]}
                    disabled={!manage}
                  />
                </div>
                <Select label={tr("Model")} value={bot.model} onChange={(v) => set("model", v)} options={Object.entries(MODELS).map(([value, label]) => ({ value, label }))} help={tr("Larger models answer harder questions better and use more weighted tokens per answer.")} disabled={!manage} />
                <Field label={tr("Welcome message")} value={bot.welcome_message} onChange={(e) => set("welcome_message", e.target.value)} maxLength={500} disabled={!manage} />
                <label className="vr-field">
                  <span className="vr-field-top">{tr("Behaviour instructions")}</span>
                  <span className="vr-input vr-textarea">
                    <textarea
                      rows={6}
                      maxLength={4000}
                      placeholder={tr("Always end with a link to the relevant help page.\nNever promise delivery dates.\nCall our product “Acme Cloud”, never “the app”.")}
                      value={bot.instructions}
                      onChange={(e) => set("instructions", e.target.value)}
                      disabled={!manage}
                    />
                  </span>
                  <span className="vr-help">{tr("Added to every answer. The assistant still answers only from your knowledge base.")}</span>
                </label>
                <Field label={tr("When nothing relevant is found")} value={bot.no_answer_message} onChange={(e) => set("no_answer_message", e.target.value)} maxLength={500} disabled={!manage} help={tr("Shown without calling the AI, with an offer to talk to the team.")} />
                <Field
                  label={tr("Fallback message")}
                  value={bot.fallback_message}
                  onChange={(e) => set("fallback_message", e.target.value)}
                  maxLength={500}
                  disabled={!manage}
                  help={tr("Shown when AI is unavailable: the token allowance is used up, the subscription lapsed or the AI provider is down.")}
                />
              </div>
            </Card>
          ) : null}
          {tab === "appearance" ? (
            <Card title={tr("Look and feel")}>
              <div className="vr-form">
                <div className="vr-form-row" style={{ gridTemplateColumns: "auto 1fr", alignItems: "end" }}>
                  <label className="vr-field">
                    <span className="vr-field-top">{tr("Colour")}</span>
                    <input
                      type="color"
                      className="vr-color"
                      value={bot.appearance.primary_color}
                      onChange={(e) => set("appearance", { ...bot.appearance, primary_color: e.target.value })}
                      disabled={!manage}
                      aria-label={tr("Primary colour")}
                    />
                  </label>
                  <Field label={tr("Hex")} className="vr-mono" value={bot.appearance.primary_color} onChange={(e) => set("appearance", { ...bot.appearance, primary_color: e.target.value })} disabled={!manage} />
                </div>
                <div className="vr-form-row" style={{ gridTemplateColumns: "1fr 1fr" }}>
                  <Select
                    label={tr("Position")}
                    value={bot.appearance.position}
                    onChange={(v) => set("appearance", { ...bot.appearance, position: v as "left" | "right" })}
                    options={[
                      { value: "right", label: tr("Bottom right") },
                      { value: "left", label: tr("Bottom left") },
                    ]}
                    disabled={!manage}
                  />
                  <Select
                    label={tr("Theme")}
                    value={bot.appearance.theme}
                    onChange={(v) => set("appearance", { ...bot.appearance, theme: v as "light" | "dark" | "auto" })}
                    options={[
                      { value: "light", label: tr("Light") },
                      { value: "dark", label: tr("Dark") },
                      { value: "auto", label: tr("Follow the visitor’s system") },
                    ]}
                    disabled={!manage}
                  />
                </div>
                <Field label={tr("Launcher label")} maxLength={30} value={bot.appearance.launcher_label} onChange={(e) => set("appearance", { ...bot.appearance, launcher_label: e.target.value })} disabled={!manage} />
                <div className="vr-field">
                  <span className="vr-field-top">{tr("Logo")}</span>
                  <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                    {bot.logo_url ? <img src={bot.logo_url} alt="" width={40} height={40} style={{ borderRadius: 10, objectFit: "cover", border: "1px solid var(--line)" }} /> : null}
                    {manage ? (
                      <label className="vr-btn vr-btn-secondary vr-btn-sm">
                        <span>{bot.logo_url ? tr("Replace") : tr("Upload")} {tr("logo")}</span>
                        <input
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          hidden
                          onChange={async (e) => {
                            const f = e.target.files?.[0];
                            e.target.value = "";
                            if (!f) return;
                            try {
                              const b = await api.upload<Chatbot>(`/chatbots/${id}/logo`, f);
                              data.setData({ ...b, snippet: bot.snippet });
                              setMsg({ tone: "brand", text: tr("Logo updated.") });
                            } catch (e2) {
                              setMsg({ tone: "danger", text: errorText(e2) });
                            }
                          }}
                        />
                      </label>
                    ) : null}
                    {manage && bot.logo_url ? (
                      <Button size="sm" variant="ghost" onClick={async () => data.setData({ ...(await api.del<Chatbot>(`/chatbots/${id}/logo`)), snippet: bot.snippet })}>
                        {tr("Remove")}
                      </Button>
                    ) : null}
                  </div>
                  <span className="vr-help">{tr("PNG, JPEG or WebP, up to 512 KB. Square images work best.")}</span>
                </div>
              </div>
            </Card>
          ) : null}
          {tab === "install" ? (
            <>
              <Card title={tr("Authorized domains")}>
                <label className="vr-field">
                  <span className="vr-input vr-textarea">
                    <textarea rows={4} value={domains} onChange={(e) => setDomains(e.target.value)} placeholder={tr("example.com\n*.example.com")} disabled={!manage} aria-label={tr("Domains, one per line")} />
                  </span>
                  <span className="vr-help">
                    {tr("One per line.")} <span className="vr-mono">{tr("example.com")}</span> {tr("covers example.com and www.example.com;")} <span className="vr-mono">{tr("*.example.com")}</span> {tr("covers every subdomain. On any other site the chatbot doesn’t load.")}
                  </span>
                </label>
                {manage ? (
                  <div style={{ marginTop: 12 }}>
                    <Button busy={busy} onClick={() => save({ allowed_domains: domains.split(/[\n,]/).map((d) => d.trim()).filter(Boolean) }, "Domains saved.")}>
                      {tr("Save domains")}
                    </Button>
                  </div>
                ) : null}
              </Card>
              <Card title={tr("Install on your site")}>
                <p className="vr-muted" style={{ marginTop: 0 }}>
                  {tr("Paste this before")} <span className="vr-mono">{tr("</body>")}</span> {tr("on every page where the chatbot should appear.")}
                </p>
                <pre className="vr-code" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                  {data.data?.snippet}
                </pre>
                <Button size="sm" icon="copy" onClick={() => navigator.clipboard?.writeText(data.data?.snippet ?? "")}>
                  {tr("Copy snippet")}
                </Button>
              </Card>
            </>
          ) : null}
          {tab === "handoff" ? (
            <Card title={tr("Talking to a person")}>
              <Switch
                label={tr("Let customers ask for a person")}
                help={tr("Customers can hand the conversation to your team. It’s offered automatically when the assistant can’t answer.")}
                checked={bot.escalation.enabled}
                onChange={(v) => set("escalation", { ...bot.escalation, enabled: v })}
                disabled={!manage}
              />
              <Switch
                label={tr("Offer a ticket form if nobody replies")}
                help={tr("If no one from the team answers in time, the chat offers the form that fits the conversation, or the contact form. Sending it opens a ticket and the assistant takes the chat back.")}
                checked={bot.escalation.ticket_fallback ?? true}
                onChange={(v) => set("escalation", { ...bot.escalation, ticket_fallback: v })}
                disabled={!manage || !bot.escalation.enabled}
              />
              {bot.escalation.ticket_fallback ?? true ? (
                <div className="vr-form-row" style={{ marginTop: 12 }}>
                  <Field
                    label={tr("Wait before offering it (seconds)")}
                    type="number"
                    min={10}
                    max={3600}
                    className="vr-mono"
                    value={bot.escalation.wait_seconds ?? 120}
                    disabled={!manage || !bot.escalation.enabled}
                    onChange={(e) => set("escalation", { ...bot.escalation, wait_seconds: Number(e.target.value) })}
                  />
                  <Select
                    label={tr("When no other form fits")}
                    value={bot.escalation.fallback_form_id ?? ""}
                    disabled={!manage || !bot.escalation.enabled}
                    onChange={(v) => set("escalation", { ...bot.escalation, fallback_form_id: v || null })}
                    options={[{ value: "", label: tr("Contact the team (built in)") }, ...(forms.data ?? []).filter((x) => x.status === "active" && !x.is_contact).map((x) => ({ value: x.id, label: x.name }))]}
                  />
                </div>
              ) : (
                <Switch
                  label={tr("Ask for their email first")}
                  help={tr("So the team can reach them if they leave before someone replies.")}
                  checked={bot.escalation.ask_email}
                  onChange={(v) => set("escalation", { ...bot.escalation, ask_email: v })}
                  disabled={!manage || !bot.escalation.enabled}
                />
              )}
              <p className="vr-help" style={{ marginTop: 12 }}>
                {tr("Members whose role can reply to conversations get an in-app and email notice. While someone has the conversation, the assistant stays quiet; resolving it hands it back.")}{" "}
                <Link className="vr-link" href="/forms">
                  {tr("Manage forms →")}
                </Link>
              </p>
            </Card>
          ) : null}
          {tab === "tools" ? <BotTools botId={bot.id} manage={manage} /> : null}
          {manage ? (
            <Card title={tr("Danger zone")}>
              <Button
                variant="danger"
                onClick={async () => {
                  if (!confirm(tr("Delete {name}? It stops working on your site at once. Conversations are deleted too.", { name: bot.name }))) return;
                  await api.del(`/chatbots/${id}`);
                  router.push("/chatbots");
                }}
              >
                {tr("Delete chatbot")}
              </Button>
            </Card>
          ) : null}
        </div>
        <WidgetPreview bot={bot} />
      </div>
    </>
  );
}

/** A static preview in the chatbot's colours. The real widget is the /widget page inside an iframe. */
function WidgetPreview({ bot }: { bot: Chatbot }) {
  const dark = bot.appearance.theme === "dark";
  return (
    <aside className="vr-rail" aria-label={tr("Preview")}>
      <span className="vr-eyebrow">{tr("Preview")}</span>
      <div className="vr-wpreview" data-theme={dark ? "dark" : "light"} style={{ ["--w-brand" as string]: bot.appearance.primary_color }}>
        <div className="vr-wpreview-head">
          {bot.logo_url ? <img src={bot.logo_url} alt="" width={28} height={28} /> : <span className="vr-wpreview-dot" />}
          <b>{bot.name}</b>
        </div>
        <div className="vr-wpreview-body">
          <div className="vr-wmsg is-bot">{bot.welcome_message}</div>
          <div className="vr-wmsg is-me">{tr("How long do refunds take?")}</div>
          <div className="vr-wmsg is-bot">
            {tr("Refunds are processed within five business days")}<span className="vr-cite">1</span>.
          </div>
        </div>
        <div className="vr-wpreview-input">
          <span>{tr("Type a message…")}</span>
          <span className="vr-wpreview-send" />
        </div>
      </div>
      <div style={{ display: "flex", justifyContent: bot.appearance.position === "left" ? "flex-start" : "flex-end" }}>
        <span className="vr-wlauncher" style={{ background: bot.appearance.primary_color }}>
          {bot.appearance.launcher_label}
        </span>
      </div>
    </aside>
  );
}

function BotTools({ botId, manage }: { botId: string; manage: boolean }) {
  const { can } = useSession();
  const data = useData<{ items: BotTool[] }>(`/chatbots/${botId}/tools`);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (data.loading && !data.data) return <Spinner />;
  const items = data.data?.items ?? [];
  async function toggle(id: string, on: boolean) {
    setBusy(id);
    setErr(null);
    try {
      const ids = items.filter((t) => (t.id === id ? on : t.enabled)).map((t) => t.id);
      data.setData(await api.put<{ items: BotTool[] }>(`/chatbots/${botId}/tools`, { tool_ids: ids }));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
    }
  }
  return (
    <Card title={tr("API tools")}>
      <p className="vr-help" style={{ marginTop: 0 }}>
        {tr("The assistant calls a tool only when a question needs it, such as “where is my order?”. Every call shows in the conversation’s “How it was answered”, and the visitor never sees the tool or its key. Calls use AI tokens for the decision, shown as “API tool decisions” on the usage page.")}
      </p>
      {err ? <Banner tone="danger">{err}</Banner> : null}
      {items.length ? (
        items.map((t) => (
          <Switch key={t.id} label={t.display_name} help={t.description} checked={t.enabled} disabled={!manage || busy === t.id} onChange={(on) => toggle(t.id, on)} />
        ))
      ) : (
        <span className="vr-help">{tr("No API tool is turned on yet.")}</span>
      )}
      {can("integrations.manage") ? (
        <p className="vr-help">
          <Link className="vr-link" href="/tools">
            {tr("Manage API tools →")}
          </Link>
        </p>
      ) : null}
    </Card>
  );
}
