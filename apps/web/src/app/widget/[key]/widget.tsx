"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { ApiError, streamEvents, type ChatMessage } from "@/lib/api";

type Config = {
  name: string;
  workspace: string;
  appearance: { primary_color: string; position: "left" | "right"; launcher_label: string; theme: "light" | "dark" | "auto" };
  logo_url: string | null;
  language: string;
  welcome_message: string;
  escalation: { enabled: boolean; ask_email: boolean };
  ai_available: boolean;
  fallback_message: string;
};
type Msg = Omit<ChatMessage, "model" | "trace" | "latency_ms">;
type Stored = { conversation_id: string; visitor_token: string };

const COPY: Record<string, Record<string, string>> = {
  en: { placeholder: "Type a message…", human: "Talk to a person", email: "Your email", send: "Send", handed: "We’ve passed this to the team. You’ll get a reply here, and by email if you leave.", waiting: "Waiting for the team…", joined: "joined the chat", resolved: "This conversation was resolved. Write again any time.", close: "Close chat", sources: "Sources", new: "New chat", offline: "This chat isn’t available on this website." },
  de: { placeholder: "Nachricht eingeben…", human: "Mit einer Person sprechen", email: "Ihre E-Mail", send: "Senden", handed: "Wir haben das an unser Team weitergegeben. Sie erhalten hier eine Antwort, und per E-Mail, falls Sie gehen.", waiting: "Warten auf das Team…", joined: "ist dem Chat beigetreten", resolved: "Dieses Gespräch wurde abgeschlossen. Schreiben Sie jederzeit erneut.", close: "Chat schließen", sources: "Quellen", new: "Neuer Chat", offline: "Dieser Chat ist auf dieser Website nicht verfügbar." },
  fr: { placeholder: "Écrivez un message…", human: "Parler à une personne", email: "Votre e-mail", send: "Envoyer", handed: "Nous avons transmis votre demande à l’équipe. Vous recevrez une réponse ici, et par e-mail si vous partez.", waiting: "En attente de l’équipe…", joined: "a rejoint la conversation", resolved: "Cette conversation est résolue. Écrivez-nous à tout moment.", close: "Fermer le chat", sources: "Sources", new: "Nouvelle conversation", offline: "Ce chat n’est pas disponible sur ce site." },
  es: { placeholder: "Escribe un mensaje…", human: "Hablar con una persona", email: "Tu correo", send: "Enviar", handed: "Hemos pasado tu consulta al equipo. Recibirás respuesta aquí, y por correo si te vas.", waiting: "Esperando al equipo…", joined: "se unió al chat", resolved: "Esta conversación se resolvió. Escribe cuando quieras.", close: "Cerrar chat", sources: "Fuentes", new: "Nuevo chat", offline: "Este chat no está disponible en este sitio." },
  fa: { placeholder: "پیام خود را بنویسید…", human: "گفتگو با یک نفر", email: "ایمیل شما", send: "ارسال", handed: "درخواست شما به تیم ما سپرده شد. پاسخ را همین‌جا و در صورت ترک صفحه، با ایمیل دریافت می‌کنید.", waiting: "در انتظار تیم…", joined: "به گفتگو پیوست", resolved: "این گفتگو بسته شد. هر زمان خواستید دوباره بنویسید.", close: "بستن گفتگو", sources: "منابع", new: "گفتگوی جدید", offline: "این گفتگو در این وب‌سایت در دسترس نیست." },
};

function t(lang: string, k: string) {
  const l = lang === "auto" ? (typeof navigator !== "undefined" ? navigator.language.slice(0, 2) : "en") : lang;
  return (COPY[l] ?? COPY.en)[k] ?? COPY.en[k];
}

function storageKey(key: string) {
  return `verity.widget.${key}`;
}
function load(key: string): Stored | null {
  try {
    const raw = localStorage.getItem(storageKey(key));
    return raw ? (JSON.parse(raw) as Stored) : null;
  } catch {
    return null;
  }
}
function persist(key: string, v: Stored | null) {
  try {
    if (v) localStorage.setItem(storageKey(key), JSON.stringify(v));
    else localStorage.removeItem(storageKey(key));
  } catch {}
}

/** The embedding page's origin: the browser's own record when it has one, else what the loader passed. */
function parentOrigin(passed: string) {
  try {
    const ao = (window.location as Location & { ancestorOrigins?: DOMStringList }).ancestorOrigins;
    if (ao && ao.length) return ao[0];
  } catch {}
  return passed;
}

export function Widget({ chatKey, origin, page }: { chatKey: string; origin: string; page: string }) {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [blocked, setBlocked] = useState(false);
  const [open, setOpen] = useState(false);
  const [conv, setConv] = useState<Stored | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [status, setStatus] = useState<"open" | "escalated" | "resolved">("open");
  const [streaming, setStreaming] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [email, setEmail] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const po = useRef("");

  const base = `/api/widget/${encodeURIComponent(chatKey)}`;
  const headers = useCallback((token?: string): Record<string, string> => {
    const h: Record<string, string> = { "X-Verity-Parent-Origin": po.current };
    if (token) h["X-Visitor-Token"] = token;
    return h;
  }, []);

  const call = useCallback(
    async <T,>(path: string, init: RequestInit & { token?: string } = {}): Promise<T> => {
      const res = await fetch(`${base}${path}`, {
        ...init,
        headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...headers(init.token) },
        cache: "no-store",
      });
      const json = await res.json().catch(() => null);
      if (!res.ok) throw new ApiError(res.status, json?.detail ?? {});
      return json as T;
    },
    [base, headers],
  );

  // Config, theme and the saved conversation.
  useEffect(() => {
    po.current = parentOrigin(origin);
    if (window.top === window.self && process.env.NODE_ENV === "production") {
      setBlocked(true);
      return;
    }
    call<Config>("/config")
      .then((c) => {
        setCfg(c);
        document.documentElement.dataset.theme = c.appearance.theme === "auto" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : c.appearance.theme;
        document.documentElement.lang = c.language === "auto" ? navigator.language.slice(0, 2) : c.language;
        if (c.language === "fa" || c.language === "ar" || c.language === "he") document.documentElement.dir = "rtl";
        setConv(load(chatKey));
      })
      .catch(() => setBlocked(true));
  }, [call, chatKey, origin]);

  useEffect(() => {
    if (!cfg) return;
    window.parent.postMessage({ verity: true, type: "layout", open, position: cfg.appearance.position }, "*");
  }, [open, cfg]);

  const lastAt = messages.length ? messages[messages.length - 1].created_at : null;
  const poll = useCallback(async () => {
    if (!conv) return;
    try {
      const d = await call<{ status: typeof status; messages: Msg[] }>(`/conversations/${conv.conversation_id}`, { token: conv.visitor_token });
      setMessages(d.messages);
      setStatus(d.status);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        persist(chatKey, null);
        setConv(null);
        setMessages([]);
      }
    }
  }, [conv, call, chatKey]);

  useEffect(() => {
    if (conv && open) poll();
  }, [conv, open, poll]);
  // While the team has the conversation, check for their replies every few seconds.
  useEffect(() => {
    if (!open || status !== "escalated") return;
    const id = setInterval(poll, 4000);
    return () => clearInterval(id);
  }, [open, status, poll]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, streaming, lastAt]);

  async function ensureConversation(): Promise<Stored> {
    if (conv) return conv;
    const c = await call<Stored>("/conversations", { method: "POST", body: JSON.stringify({ page_url: page }) });
    persist(chatKey, c);
    setConv(c);
    return c;
  }

  async function send(e?: React.FormEvent) {
    e?.preventDefault();
    const q = text.trim();
    if (!q || busy) return;
    setBusy(true);
    setErr(null);
    setText("");
    try {
      const c = await ensureConversation();
      setStreaming(null);
      await streamEvents(
        `${base}/conversations/${c.conversation_id}/messages`,
        { text: q },
        (event, data) => {
          if (event === "message") {
            const m = data as Msg;
            setMessages((prev) => [...prev.filter((x) => x.id !== m.id), m]);
            if (m.role === "visitor") setStreaming("");
            else setStreaming(null);
          } else if (event === "delta") setStreaming((s) => (s ?? "") + (data as { text: string }).text);
          else if (event === "waiting") {
            setStreaming(null);
            setStatus("escalated");
          } else if (event === "end") setStatus((data as { status: typeof status }).status);
        },
        headers(c.visitor_token),
      );
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : "Something went wrong.");
      setText(q);
    } finally {
      setBusy(false);
      setStreaming(null);
    }
  }

  async function escalate(e?: React.FormEvent) {
    e?.preventDefault();
    setErr(null);
    try {
      const c = await ensureConversation();
      const d = await call<{ status: typeof status; messages: Msg[] }>(`/conversations/${c.conversation_id}/escalate`, {
        method: "POST",
        token: c.visitor_token,
        body: JSON.stringify({ email: email || undefined, reason: messages.some((m) => m.data.offer_escalation) ? "no_answer" : "visitor_request" }),
      });
      setMessages(d.messages);
      setStatus(d.status);
      setAsking(false);
    } catch (e2) {
      if (e2 instanceof ApiError && e2.code === "email_required") setAsking(true);
      setErr(e2 instanceof ApiError ? e2.message : "Something went wrong.");
    }
  }

  if (blocked) return <div className="vr-widget-root" data-blocked />;
  if (!cfg) return <div className="vr-widget-root" />;
  const lang = cfg.language;
  const brand = { ["--w-brand" as string]: cfg.appearance.primary_color };
  const offer = cfg.escalation.enabled && status === "open" && messages.length > 0 && messages[messages.length - 1]?.data.offer_escalation;

  if (!open)
    return (
      <div className="vr-widget-root" style={brand} data-position={cfg.appearance.position}>
        <button type="button" className="vr-wlauncher" onClick={() => setOpen(true)}>
          {cfg.logo_url ? <img src={cfg.logo_url} alt="" width={22} height={22} /> : <ChatIcon />}
          <span>{cfg.appearance.launcher_label}</span>
        </button>
      </div>
    );

  return (
    <div className="vr-widget-root is-open" style={brand}>
      <section className="vr-widget" aria-label={cfg.name}>
        <header className="vr-widget-head">
          {cfg.logo_url ? <img src={cfg.logo_url} alt="" width={28} height={28} /> : <span className="vr-wpreview-dot" />}
          <span style={{ flex: 1, minWidth: 0 }}>
            <b>{cfg.name}</b>
            <span className="vr-widget-sub">{status === "escalated" ? t(lang, "waiting") : cfg.workspace}</span>
          </span>
          {cfg.escalation.enabled && status === "open" && messages.length ? (
            <button type="button" className="vr-widget-link" onClick={() => (cfg.escalation.ask_email ? setAsking(true) : escalate())}>
              {t(lang, "human")}
            </button>
          ) : null}
          <button type="button" className="vr-widget-x" aria-label={t(lang, "close")} onClick={() => setOpen(false)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>
        <div className="vr-widget-body" aria-live="polite">
          <div className="vr-wmsg is-bot">{cfg.ai_available ? cfg.welcome_message : cfg.fallback_message}</div>
          {messages.map((m) => (
            <WidgetMessage key={m.id} m={m} lang={lang} />
          ))}
          {streaming !== null ? <div className="vr-wmsg is-bot">{streaming || <span className="vr-typing" aria-label="Typing"><i /><i /><i /></span>}</div> : null}
          {offer && !asking ? (
            <button type="button" className="vr-widget-chip" onClick={() => (cfg.escalation.ask_email ? setAsking(true) : escalate())}>
              {t(lang, "human")}
            </button>
          ) : null}
          {asking ? (
            <form className="vr-widget-email" onSubmit={escalate}>
              <input type="email" required placeholder={t(lang, "email")} value={email} onChange={(e) => setEmail(e.target.value)} aria-label={t(lang, "email")} autoFocus />
              <button type="submit">{t(lang, "send")}</button>
            </form>
          ) : null}
          {err ? <div className="vr-widget-err" role="alert">{err}</div> : null}
          <div ref={endRef} />
        </div>
        <form className="vr-widget-input" onSubmit={send}>
          <textarea
            rows={1}
            value={text}
            placeholder={t(lang, "placeholder")}
            aria-label={t(lang, "placeholder")}
            maxLength={4000}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) send(e);
            }}
          />
          <button type="submit" disabled={busy || !text.trim()} aria-label={t(lang, "send")}>
            {busy ? <span className="vr-spin vr-spin-sm" aria-hidden="true" /> : <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h13M12 5l7 7-7 7" /></svg>}
          </button>
        </form>
        <p className="vr-widget-foot">Answers come from {cfg.workspace}’s documentation · Verity</p>
      </section>
    </div>
  );
}

function WidgetMessage({ m, lang }: { m: Msg; lang: string }) {
  if (m.kind === "event") {
    const text = m.data.event === "escalated" ? t(lang, "handed") : m.data.event === "resolved" ? t(lang, "resolved") : `${m.data.name ?? ""} ${t(lang, "joined")}`;
    return <div className="vr-wevent">{text}</div>;
  }
  if (m.role === "visitor") return <div className="vr-wmsg is-me">{m.content}</div>;
  if (m.role === "agent")
    return (
      <div className="vr-wmsg is-agent">
        <span className="vr-wmsg-author">{m.author ?? "Team"}</span>
        {m.content}
      </div>
    );
  const blocks = m.data.blocks?.length ? m.data.blocks : [{ text: m.content, cites: [] as number[] }];
  return (
    <div className="vr-wmsg is-bot">
      <span style={{ whiteSpace: "pre-line" }}>
        {blocks.map((b, i) => (
          <Fragment key={i}>
            {b.text}
            {b.cites.map((n) => {
              const c = m.citations.find((x) => x.n === n);
              return c?.url ? (
                <a key={n} className="vr-cite" href={c.url} target="_blank" rel="noopener noreferrer" title={c.title}>
                  {n}
                </a>
              ) : (
                <span key={n} className="vr-cite" title={c ? `${c.title}${c.section ? `, ${c.section}` : ""}` : undefined}>
                  {n}
                </span>
              );
            })}
          </Fragment>
        ))}
      </span>
      {m.citations.length ? (
        <span className="vr-wsources">
          <span className="vr-wsources-label">{t(lang, "sources")}</span>
          {m.citations.map((c) =>
            c.url ? (
              <a key={c.n} href={c.url} target="_blank" rel="noopener noreferrer">
                {c.n}. {c.title}
              </a>
            ) : (
              <span key={c.n}>
                {c.n}. {c.title}
                {c.section ? `, ${c.section}` : ""}
              </span>
            ),
          )}
        </span>
      ) : null}
    </div>
  );
}

function ChatIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" />
    </svg>
  );
}
