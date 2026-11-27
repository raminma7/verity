"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { ChatFormFields, emptyValue, type ChatFormCopy } from "@/components/chat-form";
import { ApiError, streamEvents, type ChatMessage, type FormValue } from "@/lib/api";

type Config = {
  name: string;
  workspace: string;
  appearance: { primary_color: string; position: "left" | "right"; launcher_label: string; theme: "light" | "dark" | "auto" };
  logo_url: string | null;
  language: string;
  welcome_message: string;
  escalation: { enabled: boolean; ask_email: boolean; ticket_fallback?: boolean };
  ai_available: boolean;
  fallback_message: string;
};
type Msg = Omit<ChatMessage, "model" | "trace" | "latency_ms">;
type Stored = { conversation_id: string; visitor_token: string };
type Poll = { status: "open" | "escalated" | "resolved"; has_open_ticket?: boolean; has_email?: boolean; messages: Msg[] };
type FormChoice = { id: string; title: string; description: string };

const COPY: Record<string, Record<string, string>> = {
  en: { placeholder: "Type a message…", human: "Talk to a person", email: "Your email", send: "Send", handed: "We’ve passed this to the team. You’ll get a reply here.", waiting: "Waiting for the team…", joined: "joined the chat", resolved: "This conversation was resolved. Write again any time.", close: "Close chat", sources: "Sources", new: "New chat", offline: "This chat isn’t available on this website.", fill: "Fill in the form", later: "Not now", handoffIntro: "No one from the team is free right now. Leave your details and we’ll open a ticket, so someone can get back to you.", offerIntro: "This form gets your request to the right people.", ticket: "Your request is registered as ticket #{n}. Someone from our support team will contact you soon.", sent: "Sent · ticket #{n}", closedForm: "You closed this form.", request: "Leave a request", optional: "Optional", submit: "Confirm and send", choose: "Choose…", prefilled: "We filled in what you told us in the chat.", review: "Check the details, then confirm. Nothing is sent until you do.", fix: "Some fields need attention.", required: "This field is required.", leaveEmail: "Might you leave before someone replies? Leave your email and we’ll open a ticket so the team can reach you.", emailSaved: "Thanks. If nobody replies in time, we’ll open a ticket and the team will get back to you at {email}.", ticketAuto: "Nobody from the team was free in time, so we opened ticket #{n}. The team will get back to you at {email}." },
  de: { placeholder: "Nachricht eingeben…", human: "Mit einer Person sprechen", email: "Ihre E-Mail", send: "Senden", handed: "Wir haben das an unser Team weitergegeben. Sie erhalten hier eine Antwort.", waiting: "Warten auf das Team…", joined: "ist dem Chat beigetreten", resolved: "Dieses Gespräch wurde abgeschlossen. Schreiben Sie jederzeit erneut.", close: "Chat schließen", sources: "Quellen", new: "Neuer Chat", offline: "Dieser Chat ist auf dieser Website nicht verfügbar.", fill: "Formular ausfüllen", later: "Nicht jetzt", handoffIntro: "Gerade ist niemand aus dem Team frei. Hinterlassen Sie Ihre Angaben, dann eröffnen wir ein Ticket und melden uns bei Ihnen.", offerIntro: "Mit diesem Formular landet Ihre Anfrage bei den richtigen Leuten.", ticket: "Ihre Anfrage ist als Ticket #{n} registriert. Unser Support-Team meldet sich bald bei Ihnen.", sent: "Gesendet · Ticket #{n}", closedForm: "Sie haben dieses Formular geschlossen.", request: "Anfrage hinterlassen", optional: "Optional", submit: "Bestätigen und senden", choose: "Auswählen…", prefilled: "Wir haben eingetragen, was Sie im Chat gesagt haben.", review: "Prüfen Sie die Angaben und bestätigen Sie. Erst dann wird etwas gesendet.", fix: "Einige Felder brauchen Ihre Aufmerksamkeit.", required: "Dieses Feld ist erforderlich.", leaveEmail: "Müssen Sie vielleicht gehen, bevor jemand antwortet? Hinterlassen Sie Ihre E-Mail, dann eröffnen wir ein Ticket und das Team meldet sich bei Ihnen.", emailSaved: "Danke. Wenn niemand rechtzeitig antwortet, eröffnen wir ein Ticket und das Team meldet sich unter {email}.", ticketAuto: "Gerade war niemand aus dem Team frei, deshalb haben wir Ticket #{n} eröffnet. Das Team meldet sich unter {email} bei Ihnen." },
  fr: { placeholder: "Écrivez un message…", human: "Parler à une personne", email: "Votre e-mail", send: "Envoyer", handed: "Nous avons transmis votre demande à l’équipe. Vous recevrez une réponse ici.", waiting: "En attente de l’équipe…", joined: "a rejoint la conversation", resolved: "Cette conversation est résolue. Écrivez-nous à tout moment.", close: "Fermer le chat", sources: "Sources", new: "Nouvelle conversation", offline: "Ce chat n’est pas disponible sur ce site.", fill: "Remplir le formulaire", later: "Plus tard", handoffIntro: "Personne de l’équipe n’est disponible pour le moment. Laissez vos coordonnées : nous ouvrons un ticket et revenons vers vous.", offerIntro: "Ce formulaire transmet votre demande aux bonnes personnes.", ticket: "Votre demande est enregistrée sous le ticket #{n}. Notre équipe support vous contactera bientôt.", sent: "Envoyé · ticket #{n}", closedForm: "Vous avez fermé ce formulaire.", request: "Laisser une demande", optional: "Facultatif", submit: "Confirmer et envoyer", choose: "Choisir…", prefilled: "Nous avons repris ce que vous avez dit dans le chat.", review: "Vérifiez les informations, puis confirmez. Rien n’est envoyé avant.", fix: "Certains champs sont à corriger.", required: "Ce champ est obligatoire.", leaveEmail: "Vous risquez de partir avant une réponse ? Laissez votre e-mail : nous ouvrirons un ticket pour que l’équipe puisse vous recontacter.", emailSaved: "Merci. Si personne ne répond à temps, nous ouvrirons un ticket et l’équipe vous répondra à {email}.", ticketAuto: "Personne de l’équipe n’était disponible à temps : nous avons ouvert le ticket #{n}. L’équipe vous répondra à {email}." },
  es: { placeholder: "Escribe un mensaje…", human: "Hablar con una persona", email: "Tu correo", send: "Enviar", handed: "Hemos pasado tu consulta al equipo. Recibirás respuesta aquí.", waiting: "Esperando al equipo…", joined: "se unió al chat", resolved: "Esta conversación se resolvió. Escribe cuando quieras.", close: "Cerrar chat", sources: "Fuentes", new: "Nuevo chat", offline: "Este chat no está disponible en este sitio.", fill: "Rellenar el formulario", later: "Ahora no", handoffIntro: "Ahora mismo no hay nadie del equipo disponible. Deja tus datos y abriremos un ticket para que alguien te responda.", offerIntro: "Este formulario lleva tu solicitud a las personas adecuadas.", ticket: "Tu solicitud está registrada como ticket #{n}. Nuestro equipo de soporte te contactará pronto.", sent: "Enviado · ticket #{n}", closedForm: "Has cerrado este formulario.", request: "Dejar una solicitud", optional: "Opcional", submit: "Confirmar y enviar", choose: "Elegir…", prefilled: "Hemos rellenado lo que nos dijiste en el chat.", review: "Revisa los datos y confirma. No se envía nada hasta entonces.", fix: "Algunos campos necesitan tu atención.", required: "Este campo es obligatorio.", leaveEmail: "¿Quizá te vayas antes de que alguien responda? Deja tu correo y abriremos un ticket para que el equipo te contacte.", emailSaved: "Gracias. Si nadie responde a tiempo, abriremos un ticket y el equipo te escribirá a {email}.", ticketAuto: "No había nadie del equipo disponible a tiempo, así que abrimos el ticket #{n}. El equipo te escribirá a {email}." },
  fa: { placeholder: "پیام خود را بنویسید…", human: "گفتگو با یک نفر", email: "ایمیل شما", send: "ارسال", handed: "درخواست شما به تیم ما سپرده شد. پاسخ را همین‌جا دریافت می‌کنید.", waiting: "در انتظار تیم…", joined: "به گفتگو پیوست", resolved: "این گفتگو بسته شد. هر زمان خواستید دوباره بنویسید.", close: "بستن گفتگو", sources: "منابع", new: "گفتگوی جدید", offline: "این گفتگو در این وب‌سایت در دسترس نیست.", fill: "پر کردن فرم", later: "الان نه", handoffIntro: "الان کسی از تیم در دسترس نیست. مشخصات خود را بگذارید تا تیکت ثبت کنیم و با شما تماس بگیریم.", offerIntro: "این فرم درخواست شما را به افراد درست می‌رساند.", ticket: "درخواست شما با شماره‌ی تیکت #{n} ثبت شد. تیم پشتیبانی به‌زودی با شما تماس می‌گیرد.", sent: "ارسال شد · تیکت #{n}", closedForm: "این فرم را بستید.", request: "ثبت درخواست", optional: "اختیاری", submit: "تأیید و ارسال", choose: "انتخاب کنید…", prefilled: "آنچه در گفتگو گفتید را وارد کردیم.", review: "اطلاعات را بررسی و تأیید کنید. تا تأیید نکنید چیزی ارسال نمی‌شود.", fix: "چند فیلد نیاز به اصلاح دارد.", required: "این فیلد الزامی است.", leaveEmail: "ممکن است پیش از پاسخ تیم صفحه را ببندید؟ ایمیل خود را بگذارید تا تیکت ثبت کنیم و تیم با شما تماس بگیرد.", emailSaved: "ممنون. اگر کسی به‌موقع پاسخ ندهد، تیکت ثبت می‌کنیم و تیم به {email} پاسخ می‌دهد.", ticketAuto: "کسی از تیم به‌موقع در دسترس نبود، برای همین تیکت #{n} را ثبت کردیم. تیم به {email} پاسخ می‌دهد." },
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
  const [openTicket, setOpenTicket] = useState(false);
  const [hasEmail, setHasEmail] = useState(false);
  const [savedEmail, setSavedEmail] = useState<string | null>(null);
  const [forms, setForms] = useState<FormChoice[]>([]);
  const [picking, setPicking] = useState(false);
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
      const d = await call<Poll>(`/conversations/${conv.conversation_id}`, { token: conv.visitor_token });
      setMessages(d.messages);
      setStatus(d.status);
      setOpenTicket(Boolean(d.has_open_ticket));
      setHasEmail(Boolean(d.has_email));
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
  // While the team has the conversation, check for their replies every few seconds; while a ticket is open,
  // check now and then too, so replies the team writes on the ticket reach the chat.
  useEffect(() => {
    if (!open || (status !== "escalated" && !openTicket)) return;
    const id = setInterval(poll, status === "escalated" ? 4000 : 15000);
    return () => clearInterval(id);
  }, [open, status, openTicket, poll]);
  // Forms the visitor can open themselves.
  useEffect(() => {
    if (!open || !cfg) return;
    call<FormChoice[]>("/forms").then(setForms).catch(() => setForms([]));
  }, [open, cfg, call]);
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
      const d = await call<Poll>(`/conversations/${c.conversation_id}/escalate`, {
        method: "POST",
        token: c.visitor_token,
        body: JSON.stringify({ email: email || undefined, reason: messages.some((m) => m.data.offer_escalation) ? "no_answer" : "visitor_request" }),
      });
      setMessages(d.messages);
      setStatus(d.status);
      setHasEmail(Boolean(d.has_email));
      if (email) setSavedEmail(email);
      setAsking(false);
    } catch (e2) {
      if (e2 instanceof ApiError && e2.code === "email_required") setAsking(true);
      setErr(e2 instanceof ApiError ? e2.message : "Something went wrong.");
    }
  }

  const formPath = (id: string) => `/conversations/${conv?.conversation_id}/forms/${id}`;
  const formActions: FormActions = {
    prefill: async (id) => (await call<{ values: Record<string, FormValue>; ai: boolean }>(`${formPath(id)}/prefill`, { method: "POST", token: conv?.visitor_token })),
    submit: async (id, values) => {
      const d = await call<Poll & { ticket_number: number }>(`${formPath(id)}/submit`, {
        method: "POST",
        token: conv?.visitor_token,
        body: JSON.stringify({ values, confirmed: true }),
      });
      setMessages(d.messages);
      setStatus(d.status);
      setOpenTicket(true);
    },
    dismiss: async (id) => {
      const m = await call<Msg>(`${formPath(id)}/dismiss`, { method: "POST", token: conv?.visitor_token, body: "{}" });
      setMessages((prev) => prev.map((x) => (x.id === m.id ? m : x)));
    },
  };

  async function pickForm(formId: string) {
    setPicking(false);
    setErr(null);
    try {
      const c = await ensureConversation();
      const m = await call<Msg>(`/conversations/${c.conversation_id}/forms`, { method: "POST", token: c.visitor_token, body: JSON.stringify({ form_id: formId }) });
      setMessages((prev) => [...prev.filter((x) => x.id !== m.id), m]);
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : "Something went wrong.");
    }
  }

  if (blocked) return <div className="vr-widget-root" data-blocked />;
  if (!cfg) return <div className="vr-widget-root" />;
  const lang = cfg.language;
  const brand = { ["--w-brand" as string]: cfg.appearance.primary_color };
  // A form already asks for the email; don't ask twice.
  const formWaiting = messages.some((m) => m.kind === "form" && m.data.state === "offered");
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
            <button type="button" className="vr-widget-link" onClick={() => (cfg.escalation.ask_email && !cfg.escalation.ticket_fallback ? setAsking(true) : escalate())}>
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
            <WidgetMessage key={m.id} m={m} lang={lang} actions={formActions} />
          ))}
          {streaming !== null ? <div className="vr-wmsg is-bot">{streaming || <span className="vr-typing" aria-label="Typing"><i /><i /><i /></span>}</div> : null}
          {offer && !asking ? (
            <button type="button" className="vr-widget-chip" onClick={() => (cfg.escalation.ask_email && !cfg.escalation.ticket_fallback ? setAsking(true) : escalate())}>
              {t(lang, "human")}
            </button>
          ) : null}
          {status === "escalated" && cfg.escalation.ticket_fallback !== false && !openTicket && !formWaiting ? (
            hasEmail ? (
              savedEmail ? <div className="vr-wevent">{t(lang, "emailSaved").replace("{email}", savedEmail)}</div> : null
            ) : (
              <div className="vr-wform-card">
                <span className="vr-wform-sub">{t(lang, "leaveEmail")}</span>
                <form className="vr-widget-email" onSubmit={escalate}>
                  <input type="email" required placeholder={t(lang, "email")} value={email} onChange={(e) => setEmail(e.target.value)} aria-label={t(lang, "email")} />
                  <button type="submit">{t(lang, "send")}</button>
                </form>
              </div>
            )
          ) : null}
          {asking && !cfg.escalation.ticket_fallback ? (
            <form className="vr-widget-email" onSubmit={escalate}>
              <input type="email" required placeholder={t(lang, "email")} value={email} onChange={(e) => setEmail(e.target.value)} aria-label={t(lang, "email")} autoFocus />
              <button type="submit">{t(lang, "send")}</button>
            </form>
          ) : null}
          {forms.length && status !== "escalated" && !picking ? (
            <button type="button" className="vr-widget-chip" aria-expanded={false} onClick={() => setPicking(true)}>
              {t(lang, "request")}
            </button>
          ) : null}
          {picking ? (
            <div className="vr-wpick" role="group" aria-label={t(lang, "request")}>
              {forms.map((f) => (
                <button key={f.id} type="button" className="vr-widget-chip" onClick={() => pickForm(f.id)}>
                  {f.title}
                </button>
              ))}
            </div>
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

type FormActions = {
  prefill: (id: string) => Promise<{ values: Record<string, FormValue>; ai: boolean }>;
  submit: (id: string, values: Record<string, FormValue>) => Promise<void>;
  dismiss: (id: string) => Promise<void>;
};

function formCopy(lang: string): ChatFormCopy {
  return { optional: t(lang, "optional"), submit: t(lang, "submit"), cancel: t(lang, "later"), choose: t(lang, "choose"), prefilled: t(lang, "prefilled"), review: t(lang, "review") };
}

/** A form offered in the chat: an offer card, then the fields to review, then the ticket it opened. */
function WidgetForm({ m, lang, actions }: { m: Msg; lang: string; actions: FormActions }) {
  const block = m.data.form!;
  const [stage, setStage] = useState<"offer" | "loading" | "edit">("offer");
  const [values, setValues] = useState<Record<string, FormValue>>({});
  const [prefilled, setPrefilled] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (m.data.state === "submitted")
    return (
      <div className="vr-wform-card is-done">
        <b>{block.title}</b>
        <span className="vr-wform-sub">{t(lang, "sent").replace("{n}", String(m.data.ticket_number ?? ""))}</span>
      </div>
    );
  if (m.data.state === "dismissed")
    return (
      <div className="vr-wform-card is-done">
        <b>{block.title}</b>
        <span className="vr-wform-sub">{t(lang, "closedForm")}</span>
      </div>
    );

  async function open() {
    setStage("loading");
    setMsg(null);
    const blank = Object.fromEntries(block.fields.map((f) => [f.key, emptyValue(f)]));
    try {
      const r = await actions.prefill(m.id);
      setValues({ ...blank, ...r.values });
      setPrefilled(r.ai && Object.keys(r.values).length > 0);
    } catch {
      setValues(blank);
    }
    setStage("edit");
  }

  async function submit() {
    const missing = Object.fromEntries(
      block.fields
        .filter((f) => f.required && (f.type === "checkbox" ? values[f.key] !== true : values[f.key] === "" || values[f.key] === null || (Array.isArray(values[f.key]) && !(values[f.key] as string[]).length)))
        .map((f) => [f.key, t(lang, "required")]),
    );
    if (Object.keys(missing).length) {
      setErrors(missing);
      setMsg(t(lang, "fix"));
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const clean = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "" && v !== null));
      await actions.submit(m.id, clean);
    } catch (e) {
      if (e instanceof ApiError && e.code === "invalid_form") {
        setErrors((e.data.errors as Record<string, string>) ?? {});
        setMsg(t(lang, "fix"));
      } else setMsg(e instanceof ApiError ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="vr-wform-card">
      <b>{block.title}</b>
      <span className="vr-wform-sub">{m.data.reason === "handoff_timeout" ? t(lang, "handoffIntro") : block.description || t(lang, "offerIntro")}</span>
      {stage === "offer" ? (
        <div className="vr-wform-actions">
          <button type="button" className="vr-wbtn" onClick={open}>
            {t(lang, "fill")}
          </button>
          <button type="button" className="vr-wbtn-ghost" onClick={() => actions.dismiss(m.id)}>
            {t(lang, "later")}
          </button>
        </div>
      ) : stage === "loading" ? (
        <span className="vr-typing" aria-label="Loading">
          <i />
          <i />
          <i />
        </span>
      ) : (
        <ChatFormFields
          block={{ ...block, submit_label: block.submit_label }}
          values={values}
          errors={errors}
          prefilled={prefilled}
          busy={busy}
          copy={formCopy(lang)}
          onChange={(k, v) => {
            setValues((prev) => ({ ...prev, [k]: v }));
            setMsg(null);
            setErrors((prev) => {
              const next = { ...prev };
              delete next[k];
              return next;
            });
          }}
          onSubmit={submit}
          onCancel={() => setStage("offer")}
        />
      )}
      {msg ? (
        <div className="vr-widget-err" role="alert">
          {msg}
        </div>
      ) : null}
    </div>
  );
}

function WidgetMessage({ m, lang, actions }: { m: Msg; lang: string; actions: FormActions }) {
  if (m.kind === "event") {
    if (m.data.event === "ticket_created") return null;
    const text = m.data.event === "escalated" ? t(lang, "handed") : m.data.event === "resolved" ? t(lang, "resolved") : `${m.data.name ?? ""} ${t(lang, "joined")}`;
    return <div className="vr-wevent">{text}</div>;
  }
  if (m.kind === "form" && m.data.form) return <WidgetForm m={m} lang={lang} actions={actions} />;
  if (m.role === "assistant" && m.data.notice === "ticket_created_auto")
    return <div className="vr-wmsg is-bot">{t(lang, "ticketAuto").replace("{n}", String(m.data.ticket_number ?? "")).replace("{email}", m.data.email ?? "")}</div>;
  if (m.role === "assistant" && m.data.notice === "ticket_created" && !m.data.custom)
    return <div className="vr-wmsg is-bot">{t(lang, "ticket").replace("{n}", String(m.data.ticket_number ?? ""))}</div>;
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
