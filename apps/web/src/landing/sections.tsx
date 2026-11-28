/* The landing page's sections. Server components: they translate with the request's own translator (passed down
   as `t` and `tp`), never the dashboard's global state, so several languages can render at once. The product
   components shown (AnswerCard, CitedText, SourceCard, PipelineTrace, ChatFormFields, Badge) are the real ones, with
   fixed sample content that every mock labels as a sample. Nothing here is a claim about a customer or a number. */
import Link from "next/link";
import type { ReactNode } from "react";

import type { ChatFormCopy } from "@/components/chat-form";
import { PipelineTrace } from "@/components/rag";
import { Badge, Icon, type IconName } from "@/components/ui";
import type { Dict, Locale, Translator } from "@/i18n";
import { I18nScope } from "@/i18n/scope";
import { pick } from "@/i18n/server";
import { pathFor, siteUrl } from "./site";
import { CiteDemo, FormDemo } from "./demos";
import { compact, money, storage, whole } from "./format";
import type { PublicPlan } from "./plans";
import { ThemeToggle } from "./theme-toggle";
import { ContactForm } from "./contact-form";
import { LOCALES } from "@/i18n";

export type Ctx = { t: Translator["t"]; tp: Translator["tp"]; locale: Locale; dict: Dict };

/** Strings the shared product components read themselves; only these cross to the browser. */
const SCOPE_KEYS = ["High confidence", "Partial evidence", "Low confidence", "No sources found", "Answer", "Relevance", "WEB", "Source {n}{v}", "Copy answer", "Ask again", "· Failed", "{ms} ms", "{n} source|{n} sources"] as const;

function Points({ items }: { items: string[] }) {
  return (
    <ul className="vr-land-points">
      {items.map((s) => (
        <li key={s}>
          <Icon name="check" size={16} />
          <span>{s}</span>
        </li>
      ))}
    </ul>
  );
}

function Feature({ id, flip, eyebrow, title, lead, points, children }: { id?: string; flip?: boolean; eyebrow: string; title: string; lead: string; points: string[]; children: ReactNode }) {
  return (
    <section className="vr-land-section" id={id} aria-labelledby={`${id ?? title}-h`}>
      <div className={`vr-land-wrap vr-land-feature${flip ? " is-flip" : ""}`}>
        <div className="vr-land-copy">
          <span className="vr-eyebrow">{eyebrow}</span>
          <h2 className="vr-display vr-land-h2" id={`${id ?? title}-h`}>
            {title}
          </h2>
          <p className="vr-land-lead">{lead}</p>
          <Points items={points} />
        </div>
        <div className="vr-land-visual">{children}</div>
      </div>
    </section>
  );
}

function Sample({ label, children, caption }: { label: string; children: ReactNode; caption: string }) {
  return (
    <figure className="vr-land-fig" aria-label={`${label}: ${caption}`}>
      {children}
      <figcaption className="vr-land-caption">
        <Badge>{label}</Badge>
        <span>{caption}</span>
      </figcaption>
    </figure>
  );
}

/* ——— Header and footer ——— */

export function SkipLink({ t }: Pick<Ctx, "t">) {
  return (
    <a className="vr-land-skip" href="#main">
      {t("Skip to main content")}
    </a>
  );
}

function LanguageMenu({ t, locale }: Pick<Ctx, "t" | "locale">) {
  const current = LOCALES.find((l) => l.value === locale)!;
  return (
    <details className="vr-land-lang">
      <summary aria-label={`${t("Language")}: ${current.label}`}>
        <Icon name="spark" size={14} />
        <span>{current.label}</span>
      </summary>
      <ul>
        {LOCALES.map((l) => (
          <li key={l.value}>
            <a href={pathFor(l.value)} hrefLang={l.value} lang={l.value} aria-current={l.value === locale ? "true" : undefined}>
              {l.label}
            </a>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function Header({ t, locale }: Pick<Ctx, "t" | "locale">) {
  const home = pathFor(locale);
  return (
    <header className="vr-land-head">
      <div className="vr-land-wrap vr-land-head-row">
        <a className="vr-logo" href={home} aria-label={t("Verity")}>
          {t("Verity")}
        </a>
        <nav className="vr-land-nav" aria-label={t("Main")}>
          <a href={`${home === "/" ? "" : home}#features`}>{t("Features")}</a>
          <a href={`${home === "/" ? "" : home}#how`}>{t("How it works")}</a>
          <a href={`${home === "/" ? "" : home}#pricing`}>{t("Pricing")}</a>
          <a href={`${home === "/" ? "" : home}#faq`}>{t("FAQ")}</a>
        </nav>
        <div className="vr-land-tools">
          <LanguageMenu t={t} locale={locale} />
          <ThemeToggle light={t("Light")} dark={t("Dark")} label={t("Theme")} />
          <Link className="vr-btn vr-btn-ghost vr-btn-sm vr-land-signin" href="/sign-in" prefetch={false}>
            {t("Sign in")}
          </Link>
          <Link className="vr-btn vr-btn-primary vr-btn-sm" href="/sign-up" prefetch={false}>
            {t("Create your workspace")}
          </Link>
        </div>
      </div>
    </header>
  );
}

export function Footer({ t, locale }: Pick<Ctx, "t" | "locale">) {
  const home = pathFor(locale);
  const anchor = home === "/" ? "" : home;
  return (
    <footer className="vr-land-foot">
      <div className="vr-land-wrap vr-land-foot-grid">
        <div className="vr-land-foot-brand">
          <span className="vr-logo">{t("Verity")}</span>
          <p className="vr-muted">{t("Support answers you can check.")}</p>
        </div>
        <nav aria-label={t("Product")}>
          <h2 className="vr-eyebrow">{t("Product")}</h2>
          <ul>
            <li>
              <a href={`${anchor}#features`}>{t("Features")}</a>
            </li>
            <li>
              <a href={`${anchor}#how`}>{t("How it works")}</a>
            </li>
            <li>
              <a href={`${anchor}#pricing`}>{t("Pricing")}</a>
            </li>
            <li>
              <a href={`${anchor}#faq`}>{t("FAQ")}</a>
            </li>
          </ul>
        </nav>
        <nav aria-label={t("Account")}>
          <h2 className="vr-eyebrow">{t("Account")}</h2>
          <ul>
            <li>
              <Link href="/sign-in" prefetch={false}>{t("Sign in")}</Link>
            </li>
            <li>
              <Link href="/sign-up" prefetch={false}>{t("Create your workspace")}</Link>
            </li>
            <li>
              <a href={pathFor(locale, "/privacy")}>{t("Privacy")}</a>
            </li>
          </ul>
        </nav>
        <nav aria-label={t("Language")}>
          <h2 className="vr-eyebrow">{t("Language")}</h2>
          <ul>
            {LOCALES.map((l) => (
              <li key={l.value}>
                <a href={pathFor(l.value)} hrefLang={l.value} lang={l.value} aria-current={l.value === locale ? "true" : undefined}>
                  {l.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </footer>
  );
}

/* ——— Hero ——— */

function sampleAnswer({ t, dict, locale }: Ctx) {
  return (
    <CiteDemo
      locale={locale}
      scope={pick(dict, SCOPE_KEYS)}
      question={t("How long do I have to return an order?")}
      label={t("Answer")}
      confidence="high"
      blocks={[
        { text: `${t("You can return an order within 30 days of delivery if it is unused and in its original packaging.")} `, cites: [1] },
        { text: t("Refunds go back to the original payment method within 5 working days of us receiving the return."), cites: [2] },
      ]}
      sources={[
        {
          n: 1,
          type: "pdf",
          title: t("Returns policy.pdf"),
          section: t("Returns › Eligibility"),
          excerpt: t("Orders can be returned within 30 days of delivery. Items must be unused and in their original packaging."),
          highlight: t("Orders can be returned within 30 days of delivery."),
          score: 0.91,
        },
        {
          n: 2,
          type: "web",
          title: t("Help centre · Refunds"),
          section: t("Refunds › Timing"),
          excerpt: t("Refunds are issued to the original payment method within 5 working days of us receiving the return."),
          highlight: t("within 5 working days of us receiving the return"),
          score: 0.84,
        },
      ]}
    />
  );
}

export function Hero(c: Ctx) {
  const { t } = c;
  return (
    <section className="vr-land-hero" aria-labelledby="hero-h">
      <div className="vr-land-wrap vr-land-hero-grid">
        <div className="vr-land-copy">
          <span className="vr-eyebrow">
            <Icon name="spark" size={14} />
            {t("AI customer support")}
          </span>
          <h1 className="vr-display vr-land-h1" id="hero-h">
            {t("One workspace for the whole support loop.")}
          </h1>
          <p className="vr-land-sub">{t("Your knowledge base, the chatbot on your site, live handoff to a person, tickets and email, all connected. Every AI answer cites the passage it came from.")}</p>
          <div className="vr-land-cta">
            <Link className="vr-btn vr-btn-primary vr-btn-lg" href="/sign-up" prefetch={false}>
              {t("Create your workspace")}
              <Icon name="arrow" size={16} className="vr-land-flip" />
            </Link>
            <a className="vr-btn vr-btn-secondary vr-btn-lg" href="#how">
              {t("See how it works")}
            </a>
          </div>
          <p className="vr-help">{t("Six languages, including Arabic right to left.")}</p>
        </div>
        <div className="vr-land-visual">
          <Sample label={t("Sample")} caption={t("A cited answer. The passages come from the customer’s own documents.")}>
            {sampleAnswer(c)}
          </Sample>
        </div>
      </div>
    </section>
  );
}

/* ——— The loop ——— */

export function Loop({ t }: Ctx) {
  const steps: { icon: IconName; title: string; text: string }[] = [
    { icon: "book", title: t("Knowledge base"), text: t("Documents and your crawled help centre.") },
    { icon: "chat", title: t("Chatbot"), text: t("On your site, answering with citations.") },
    { icon: "users", title: t("Live handoff"), text: t("A person takes over when the customer asks.") },
    { icon: "tag", title: t("Tickets"), text: t("Chats, forms and email land in one list.") },
    { icon: "mail", title: t("Email"), text: t("Your support mailboxes, connected.") },
  ];
  return (
    <section className="vr-land-section is-sunken" aria-labelledby="loop-h">
      <div className="vr-land-wrap">
        <div className="vr-land-head-block">
          <h2 className="vr-display vr-land-h2" id="loop-h">
            {t("From the first question to the closed ticket, in one place")}
          </h2>
          <p className="vr-land-lead">{t("Nothing to stitch together: each step hands over to the next, with the conversation, its sources and its history attached.")}</p>
        </div>
        <ol className="vr-land-loop">
          {steps.map((s, i) => (
            <li key={s.title} className="vr-card">
              <span className="vr-land-loop-top">
                <span className="vr-source-n">{i + 1}</span>
                <Icon name={s.icon} size={18} />
              </span>
              <h3>{s.title}</h3>
              <p className="vr-muted">{s.text}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ——— Strengths ——— */

export function Cited(c: Ctx) {
  const { t, dict, locale } = c;
  return (
    <Feature
      id="features"
      eyebrow={t("Answers you can check")}
      title={t("Every answer shows where it came from")}
      lead={t("The assistant cites the exact passage from your own documents, using Claude’s native citations, so a citation can’t be invented. When nothing relevant is found, it says so instead of guessing.")}
      points={[
        t("Click a citation to see the passage it points to."),
        t("Each answer carries a confidence level in words: high confidence, partial evidence, low confidence or no sources found."),
        t("“How it was answered” lists every step: embed, retrieve, generate, verify citations."),
      ]}
    >
      <Sample label={t("Sample")} caption={t("Nothing relevant was found, so nothing is made up.")}>
        <I18nScope locale={locale} strings={pick(dict, SCOPE_KEYS)}>
          <article className="vr-answer">
            <header className="vr-answer-head">
              <span className="vr-eyebrow">
                <Icon name="spark" size={14} />
                {t("Answer")}
              </span>
              <Badge tone="danger" dot>
                {t("No sources found")}
              </Badge>
            </header>
            <div className="vr-answer-body">
              <p>{t("I couldn’t find this in the documentation. Would you like to talk to a person?")}</p>
            </div>
            <footer className="vr-land-trace">
              <PipelineTrace
                steps={[
                  { name: t("Embed the question") },
                  { name: t("Retrieve passages"), detail: t("No passage above the relevance cut-off") },
                  { name: t("Generate answer"), detail: t("Skipped: nothing to cite"), state: "pending" },
                  { name: t("Verify citations"), state: "pending" },
                ]}
              />
            </footer>
          </article>
        </I18nScope>
      </Sample>
    </Feature>
  );
}

export function Control({ t, locale }: Ctx) {
  return (
    <Feature
      flip
      eyebrow={t("Humans stay in control")}
      title={t("People take over when it matters")}
      lead={t("A customer can ask for a person at any time. While an agent has the chat, the assistant goes quiet, and nobody is left waiting.")}
      points={[
        t("When nobody answers in time, a form opens a ticket automatically, with the visitor’s email if they left it."),
        t("AI-written email replies only acknowledge the request. They never promise anything and never include links."),
        t("Resolving the ticket hands the chat back to the assistant."),
      ]}
    >
      <Sample label={t("Sample")} caption={t("A handoff that nobody answered, and the ticket it opened.")}>
        <div className="vr-wpreview vr-land-chat" lang={locale}>
          <div className="vr-wpreview-head">
            <span className="vr-wpreview-dot" aria-hidden="true" />
            <b>{t("Help assistant")}</b>
          </div>
          <div className="vr-wpreview-body">
            <div className="vr-wmsg is-me">{t("Can I talk to a person?")}</div>
            <div className="vr-wevent">{t("Handed to the team. The assistant stays quiet until it’s resolved.")}</div>
            <div className="vr-wevent">{t("Nobody replied in time: ticket #{n} opened with the visitor’s email. The assistant has the chat again.", { n: 42 })}</div>
          </div>
        </div>
        <div className="vr-card vr-land-ticket">
          <div className="vr-land-ticket-top">
            <span className="vr-num">#42</span>
            <Badge tone="brand" dot>
              {t("New")}
            </Badge>
            <Badge>{t("Chat conversation")}</Badge>
          </div>
          <div className="vr-mailpreview">
            <b>{t("Automatic reply")}</b>
            <p>{t("We received your request and opened ticket #{n}. A member of the team will reply by email.", { n: 42 })}</p>
          </div>
        </div>
      </Sample>
    </Feature>
  );
}

export function Forms(c: Ctx) {
  const { t, dict, locale } = c;
  const copy: ChatFormCopy = {
    optional: t("Optional"),
    submit: t("Confirm and send"),
    cancel: t("Not now"),
    choose: t("Choose…"),
    prefilled: t("We filled in what you told us in the chat."),
    review: t("Check the details, then confirm. Nothing is sent until you do."),
  };
  return (
    <Feature
      eyebrow={t("Chatbot forms")}
      title={t("The AI offers the form. The customer confirms.")}
      lead={t("After an answer, the assistant offers the form that fits the conversation and fills it in from what the customer said. Nothing becomes a ticket until they press confirm.")}
      points={[
        t("Every value is checked again against its field before it is accepted."),
        t("Consent checkboxes are never ticked for the customer."),
        t("Without AI, forms still work: customers open them under “Leave a request”."),
      ]}
    >
      <Sample label={t("Sample")} caption={t("A prefilled form. Try editing it.")}>
        <FormDemo
          locale={locale}
          scope={pick(dict, SCOPE_KEYS)}
          copy={copy}
          caption={t("This is a sample: nothing was sent.")}
          block={{
            form_id: "sample",
            version: 1,
            title: t("Return an order"),
            description: t("Tell us what went wrong and we’ll open a ticket."),
            submit_label: t("Confirm and send"),
            fields: [
              { key: "name", label: t("Name"), type: "text", required: true, options: [], validation: {} },
              { key: "email", label: t("Email address"), type: "email", required: true, options: [], validation: {} },
              { key: "message", label: t("Message"), type: "textarea", required: true, options: [], validation: {} },
            ],
          }}
          initial={{ name: "Sam Rivera", email: "sam@example.com", message: t("The jacket I ordered is the wrong size. I would like to return it.") }}
        />
      </Sample>
    </Feature>
  );
}

export function EmailSection({ t }: Ctx) {
  const rows: { tone: "brand" | "warn" | "danger"; word: string; subject: string; note: string }[] = [
    { tone: "brand", word: t("New"), subject: t("Where is my refund?"), note: t("Category: Billing · Replied to in the same thread") },
    { tone: "warn", word: t("Possible spam"), subject: t("Wholesale price list request"), note: t("Opened as a ticket so nothing is lost") },
    { tone: "danger", word: t("Spam"), subject: t("You have won a prize"), note: t("Held for review, with the reason in words") },
  ];
  return (
    <Feature
      flip
      eyebrow={t("Email built in")}
      title={t("Your support inbox becomes tickets")}
      lead={t("Connect your support mailboxes over IMAP and SMTP. Mail becomes tickets in the right category, replies stay in the thread, and the first reply goes out automatically.")}
      points={[
        t("A trained spam filter decides without an AI call, so spam never costs tokens."),
        t("When the filter is unsure, the mail becomes a ticket marked “Possible spam”, never a lost customer."),
        t("Connections use TLS only, and mailbox passwords are encrypted and never shown again."),
      ]}
    >
      <Sample label={t("Sample")} caption={t("Incoming mail, each with a status in words.")}>
        <ul className="vr-card vr-list vr-land-mail">
          {rows.map((r) => (
            <li key={r.subject}>
              <Badge tone={r.tone} dot>
                {r.word}
              </Badge>
              <span className="vr-land-mail-text">
                <b>{r.subject}</b>
                <span className="vr-muted">{r.note}</span>
              </span>
            </li>
          ))}
        </ul>
      </Sample>
    </Feature>
  );
}

export function ToolsAndRouting(c: Ctx) {
  const { t, dict, locale } = c;
  return (
    <>
      <Feature
        eyebrow={t("API tools")}
        title={t("Answers from your own systems, with every call visible")}
        lead={t("Describe an HTTPS API once, such as order status or stock, and the chatbot can look it up in the middle of an answer. Customers see only the answer.")}
        points={[
          t("The host is fixed by your template, so nothing said in a chat can point a call elsewhere."),
          t("Keys are encrypted, never shown again, and private network addresses are refused."),
          t("Every call appears in the answer’s trace for your team: request, status, size and time."),
        ]}
      >
        <Sample label={t("Sample")} caption={t("How an answer about an order was produced.")}>
          <I18nScope locale={locale} strings={pick(dict, SCOPE_KEYS)}>
            <div className="vr-card vr-land-pad">
              <PipelineTrace
                steps={[
                  { name: t("Embed the question"), ms: 41 },
                  { name: t("Retrieve passages"), detail: t("2 passages above the cut-off"), ms: 18 },
                  { name: t("Call API · order_status"), detail: "GET /orders/{id} · 200 OK · 0.4 KB", ms: 212, kind: "tool" },
                  { name: t("Generate answer"), ms: 1380 },
                  { name: t("Verify citations"), ms: 6 },
                ]}
              />
            </div>
          </I18nScope>
        </Sample>
      </Feature>
      <Feature
        flip
        eyebrow={t("Smart routing")}
        title={t("The right team, then the least busy person")}
        lead={t("Every new ticket is routed the same way, whether it came from email, a chat, a form or a teammate. Routing uses no AI, so it keeps working when the allowance is used up.")}
        points={[
          t("Rules match the source, category, priority, words in the message or the requester’s email domain."),
          t("Inside the team, people who are available and skilled in the category come first, then whoever has the fewest open tickets."),
          t("The ticket’s timeline says why it went where it did."),
        ]}
      >
        <Sample label={t("Sample")} caption={t("A line from a ticket’s timeline.")}>
          <div className="vr-tl-item is-agent">
            <div className="vr-tl-meta">
              <Icon name="users" size={14} />
              <b>{t("Routing")}</b>
            </div>
            <p>{t("Routed to Maya Chen (rule · least busy in Billing · 2 open)")}</p>
          </div>
        </Sample>
      </Feature>
    </>
  );
}

export function Cost({ t, locale }: Ctx) {
  const used = 860_000;
  const total = 1_000_000;
  return (
    <Feature
      eyebrow={t("Predictable cost")}
      title={t("One shared AI allowance, no surprises")}
      lead={t("AI use is counted in weighted tokens shared across the whole workspace. You see the weights, and you are alerted at 80%, 90% and 100%.")}
      points={[
        t("Token packages roll over for 12 months and are used after the period’s allowance."),
        t("When the AI allowance is used up, manual ticketing, email and forms keep working, and the chatbot shows your fallback message."),
        t("Reservations are checked atomically, so parallel requests can’t overrun the allowance."),
      ]}
    >
      <Sample label={t("Sample")} caption={t("A usage meter at the first alert.")}>
        <div className="vr-card vr-land-pad">
          <div className="vr-usage">
            <div className="vr-usage-top">
              <span>{t("Weighted AI tokens this period")}</span>
              <span className="vr-mono">
                {whole(used, locale)} / {whole(total, locale)}
              </span>
            </div>
            <div className="vr-usage-track is-warn" role="meter" aria-valuemin={0} aria-valuemax={total} aria-valuenow={used} aria-label={t("Weighted AI tokens this period")}>
              <span style={{ width: `${(used / total) * 100}%` }} />
            </div>
          </div>
          <p className="vr-land-alert">
            <Badge tone="warn" dot>
              {t("80% used")}
            </Badge>
            <span className="vr-muted">{t("The workspace owners were notified by email.")}</span>
          </p>
        </div>
      </Sample>
    </Feature>
  );
}

export function More({ t }: Ctx) {
  const items: { icon: IconName; title: string; text: string }[] = [
    { icon: "chart", title: t("Analytics"), text: t("Response and resolution times, SLA targets per priority, chatbot deflection, agent workload and AI usage by feature and model.") },
    { icon: "shield", title: t("Roles and isolation"), text: t("System and custom roles, and strict separation between workspaces: every request is filtered by the caller’s own workspace.") },
    { icon: "lock", title: t("Security"), text: t("Two-factor authentication, encrypted secrets, and an audit log of changes that matter.") },
    { icon: "clock", title: t("Data you control"), text: t("Set how long closed tickets, chats and email are kept, and export your data whenever you need to.") },
    { icon: "building", title: t("Deletion with an undo"), text: t("Deleting a workspace or an account has a 30-day grace period, so a mistake can be reversed. Built for GDPR requests.") },
    { icon: "spark", title: t("Six languages"), text: t("The dashboard, system email and chatbot work in English, Spanish, German, French, Italian and Arabic, right to left. The chatbot answers in the customer’s language.") },
  ];
  return (
    <section className="vr-land-section is-sunken" aria-labelledby="more-h">
      <div className="vr-land-wrap">
        <div className="vr-land-head-block">
          <h2 className="vr-display vr-land-h2" id="more-h">
            {t("Built to be run, measured and trusted")}
          </h2>
        </div>
        <ul className="vr-land-grid">
          {items.map((i) => (
            <li key={i.title} className="vr-card vr-land-item">
              <Icon name={i.icon} size={20} />
              <h3>{i.title}</h3>
              <p className="vr-muted">{i.text}</p>
            </li>
          ))}
        </ul>
        <figure className="vr-land-lang-demo" aria-label={t("Sample: the chatbot answers in the customer’s language.")}>
          <div className="vr-wmsg is-me" lang="ar" dir="rtl">
            كم من الوقت لدي لإرجاع طلبي؟
          </div>
          <div className="vr-wmsg is-bot" lang="ar" dir="rtl">
            يمكنك إرجاع الطلب خلال 30 يومًا من التسليم إذا كان غير مستخدم وفي عبوته الأصلية.
          </div>
          <figcaption className="vr-land-caption">
            <Badge>{t("Sample")}</Badge>
            <span>{t("The chatbot answers in the customer’s language.")}</span>
          </figcaption>
        </figure>
      </div>
    </section>
  );
}

/* ——— How it works ——— */

export function How({ t }: Ctx) {
  const steps = [
    { title: t("Add your knowledge"), text: t("Upload PDF, Word, text, CSV or Excel files, or point Verity at your help centre. It re-syncs on a schedule and re-reads only the pages that changed.") },
    { title: t("Put the chatbot on your site"), text: t("Choose its name, colour, language and tone, authorize your domains, then paste one line:") },
    { title: t("Customers get cited answers, or a person"), text: t("The assistant answers from your documents and your APIs. If it can’t, or the customer asks, your team takes over.") },
    { title: t("Your team works tickets and email"), text: t("Everything lands in one list, routed to the right person, with a timeline and, if you like, an AI summary.") },
  ];
  return (
    <section className="vr-land-section" id="how" aria-labelledby="how-h">
      <div className="vr-land-wrap">
        <div className="vr-land-head-block">
          <h2 className="vr-display vr-land-h2" id="how-h">
            {t("How it works")}
          </h2>
        </div>
        <ol className="vr-land-steps">
          {steps.map((s, i) => (
            <li key={s.title}>
              <span className="vr-source-n">{i + 1}</span>
              <h3>{s.title}</h3>
              <p className="vr-muted">{s.text}</p>
              {i === 1 ? (
                <pre className="vr-land-code" dir="ltr" lang="en">
                  <code>{`<script src="${siteUrl()}/widget.js" data-verity-key="vb_…" async></script>`}</code>
                </pre>
              ) : null}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ——— Pricing ——— */

const LIMITS = ["documents", "domains", "crawled_pages", "chatbot_domains", "email_accounts", "members", "api_tools", "storage_mb"] as const;

export function Pricing({ t, locale, plans }: Ctx & { plans: PublicPlan[] | null }) {
  const labels: Record<(typeof LIMITS)[number], string> = {
    documents: t("Documents"),
    domains: t("Crawled domains"),
    crawled_pages: t("Crawled pages"),
    chatbot_domains: t("Chatbot domains"),
    email_accounts: t("Email accounts"),
    members: t("Members"),
    api_tools: t("API tools"),
    storage_mb: t("Storage"),
  };
  return (
    <section className="vr-land-section is-sunken" id="pricing" aria-labelledby="pricing-h">
      <div className="vr-land-wrap">
        <div className="vr-land-head-block">
          <h2 className="vr-display vr-land-h2" id="pricing-h">
            {t("Plans")}
          </h2>
          <p className="vr-land-lead">{t("Plans differ in their monthly AI allowance and in their limits. You choose yours after creating your workspace.")}</p>
        </div>
        {plans ? (
          <div className="vr-plans">
            {plans.map((p) => (
              <article key={p.id} className="vr-plan" aria-label={t("{name} plan", { name: p.name })}>
                <div className="vr-plan-head">
                  <h3 className="vr-h2">{p.name}</h3>
                </div>
                <div className="vr-plan-price">
                  <span className="vr-display">{money(p.price_cents, p.currency, locale)}</span>
                  <span className="vr-muted">{p.billing_cycle === "year" ? t("per year") : t("per month")}</span>
                </div>
                <div className="vr-plan-tokens">
                  <span>{p.billing_cycle === "year" ? t("{tokens} weighted AI tokens per year", { tokens: compact(p.token_allowance, locale) }) : t("{tokens} weighted AI tokens per month", { tokens: compact(p.token_allowance, locale) })}</span>
                </div>
                <dl className="vr-land-limits">
                  {LIMITS.filter((k) => k in p.limits).map((k) => (
                    <div key={k}>
                      <dt>{labels[k]}</dt>
                      <dd className="vr-mono">{p.limits[k] < 0 ? t("Unlimited") : k === "storage_mb" ? storage(p.limits[k], locale) : whole(p.limits[k], locale)}</dd>
                    </div>
                  ))}
                </dl>
                <Link className="vr-btn vr-btn-secondary vr-btn-md" href="/sign-up" prefetch={false}>
                  {t("Create your workspace")}
                </Link>
              </article>
            ))}
          </div>
        ) : (
          <p className="vr-card vr-land-pad">
            {t("Plans and prices are shown when you create your workspace.")} <Link className="vr-link" href="/sign-up" prefetch={false}>{t("Create your workspace")}</Link>
          </p>
        )}
        <p className="vr-help vr-land-note">{t("Need more AI capacity? Token packages are added to your workspace and roll over for 12 months.")}</p>
      </div>
    </section>
  );
}

/* ——— FAQ ——— */

export function Faq({ t }: Ctx) {
  const qa = [
    { q: t("What if the assistant can’t find an answer?"), a: t("When no passage in your documents is relevant enough, the chatbot says it couldn’t find the answer and offers to bring in a person, instead of guessing.") },
    { q: t("What can I add to the knowledge base?"), a: t("PDF, Word, text, CSV and Excel files, checked by their content, and websites. Crawling stays on your site, follows robots.txt and refuses private network addresses.") },
    { q: t("Which AI models does it use?"), a: t("Answers are written by Claude: each chatbot can use Claude Opus 5.5, Sonnet 5.5 or Haiku 5.5. Documents and questions are embedded with Voyage.") },
    { q: t("What happens when nobody is free to reply?"), a: t("After the wait you set, a ticket opens with the visitor’s email, or the chat offers a form, so the request is never lost. The assistant then takes the chat back.") },
    { q: t("What happens when the AI allowance is used up?"), a: t("Manual ticketing, email and forms keep working. The chatbot shows the fallback message you wrote, and token packages can top the allowance up.") },
    { q: t("Which mailboxes can I connect?"), a: t("Any mailbox that offers IMAP and SMTP, using an app password: for example Gmail, Microsoft 365 where IMAP and SMTP AUTH are allowed, or Fastmail.") },
    { q: t("How is my data protected?"), a: t("Every request is limited to the caller’s own workspace. Sessions are server-side, passwords are hashed, two-factor secrets and mailbox passwords are encrypted, and changes that matter go to an audit log.") },
    { q: t("Can I export or delete my data?"), a: t("Yes. Owners export a workspace and anyone can export their own data. Deleting a workspace or an account has a 30-day undo, and you choose how long tickets, chats and email are kept.") },
  ];
  return (
    <section className="vr-land-section" id="faq" aria-labelledby="faq-h">
      <div className="vr-land-wrap vr-land-faq-wrap">
        <div className="vr-land-head-block">
          <h2 className="vr-display vr-land-h2" id="faq-h">
            {t("Questions")}
          </h2>
        </div>
        <div className="vr-land-faq">
          {qa.map((x) => (
            <details key={x.q}>
              <summary>
                <span>{x.q}</span>
                <Icon name="chevron" size={16} />
              </summary>
              <p className="vr-muted">{x.a}</p>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ——— Closing call to action and contact ——— */

export function Closing({ t }: Ctx) {
  return (
    <section className="vr-land-section is-sunken vr-land-closing" aria-labelledby="closing-h">
      <div className="vr-land-wrap">
        <h2 className="vr-display vr-land-h2" id="closing-h">
          {t("Give your customers answers they can check.")}
        </h2>
        <div className="vr-land-cta">
          <Link className="vr-btn vr-btn-primary vr-btn-lg" href="/sign-up" prefetch={false}>
            {t("Create your workspace")}
            <Icon name="arrow" size={16} className="vr-land-flip" />
          </Link>
          <Link className="vr-btn vr-btn-secondary vr-btn-lg" href="/sign-in" prefetch={false}>
            {t("Sign in")}
          </Link>
        </div>
      </div>
    </section>
  );
}

export function Contact({ t, locale }: Ctx) {
  return (
    <section className="vr-land-section" id="contact" aria-labelledby="contact-h">
      <div className="vr-land-wrap vr-land-contact">
        <div className="vr-land-copy">
          <h2 className="vr-display vr-land-h2" id="contact-h">
            {t("Talk to us")}
          </h2>
          <p className="vr-land-lead">{t("Questions before you start? Send a message and our team will reply by email.")}</p>
          <p className="vr-help">
            {t("We use your details only to answer you.")} <a className="vr-link" href={pathFor(locale, "/privacy")}>{t("Privacy")}</a>
          </p>
        </div>
        <ContactForm
          locale={locale}
          copy={{
            name: t("Your name"),
            email: t("Your email"),
            message: t("Message"),
            send: t("Send message"),
            sending: t("Sending…"),
            sent: t("Message sent. We’ll reply by email."),
            error: t("The message couldn’t be sent. Check the details and try again."),
            limited: t("Too many messages from this address. Try again later."),
            required: t("Fill in every field."),
          }}
        />
      </div>
    </section>
  );
}
