"use client";

import { t as tr } from "@/i18n";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AttachmentChip, HELD_REASON, heldReason, MailBody } from "@/components/email";
import { Badge, Banner, Button, Card, dateTime, EmptyState, Field, Segmented, Spinner } from "@/components/ui";
import { api, errorText, type HeldEmail } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Listing = { items: HeldEmail[]; counts: Record<string, number> };

export default function SpamPage() {
  const { can } = useSession();
  const review = can("email.review");
  const [reason, setReason] = useState("");
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setTerm(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);
  const query = new URLSearchParams();
  if (reason) query.set("reason", reason);
  if (term) query.set("q", term);
  const data = useData<Listing>(review ? `/email/spam?${query.toString()}` : null);
  const [openId, setOpenId] = useState<string | null>(null);
  const detail = useData<HeldEmail>(openId ? `/email/messages/${openId}` : null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [allow, setAllow] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: React.ReactNode } | null>(null);

  if (!review) return <Banner tone="neutral">{tr("Your role can’t review spam.")}</Banner>;
  const items = data.data?.items ?? [];
  const counts = data.data?.counts ?? { spam: 0, promotion: 0, rate_limited: 0 };
  const total = counts.spam + counts.promotion + counts.rate_limited;

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }

  async function notSpam(m: HeldEmail) {
    await act(`ok-${m.id}`, async () => {
      const r = await api.post<{ ticket: { id: string; number: number } | null }>(`/email/messages/${m.id}/not-spam`, { allow_sender: allow });
      setOpenId(null);
      await data.reload();
      setMsg({
        tone: "brand",
        text: r.ticket ? (
          <>
            {tr("Moved to tickets as")}{" "}
            <Link className="vr-link" href={`/tickets/${r.ticket.id}`}>
              #{r.ticket.number}
            </Link>
            {allow ? tr(", and {from_addr} is now always allowed.", { from_addr: m.from_addr }) : "."}
          </>
        ) : (
          tr("Moved to tickets.")
        ),
      });
    });
  }

  async function remove(ids: string[]) {
    await act("delete", async () => {
      if (ids.length === 1) await api.del(`/email/messages/${ids[0]}`);
      else await api.post("/email/spam/delete", { ids });
      setPicked(new Set());
      if (openId && ids.includes(openId)) setOpenId(null);
      await data.reload();
      setMsg({ tone: "brand", text: ids.length === 1 ? tr("Deleted.") : tr("{length} emails deleted.", { length: ids.length }) });
    });
  }

  const toggle = (id: string) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  };
  const open = detail.data && openId === detail.data.id ? detail.data : null;

  return (
    <>
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      <div className="vr-filters">
        <Segmented
          label={tr("Show")}
          value={reason}
          onChange={setReason}
          options={[
            { value: "", label: tr("All · {total}", { total: total }) },
            { value: "spam", label: tr("Spam · {spam}", { spam: counts.spam }) },
            { value: "promotion", label: tr("Promotions · {promotion}", { promotion: counts.promotion }) },
            { value: "rate_limited", label: tr("Held · {rate_limited}", { rate_limited: counts.rate_limited }) },
          ]}
        />
        <Field className="vr-grow" icon="search" placeholder={tr("Search sender or subject")} value={q} onChange={(e) => setQ(e.target.value)} aria-label={tr("Search spam")} />
        {picked.size ? (
          <Button variant="danger" busy={busy === "delete"} onClick={() => remove(Array.from(picked))}>
            {tr("Delete")} {picked.size}
          </Button>
        ) : null}
      </div>
      <div className="vr-spam">
        <Card padded={false} title={tr("Held email")}>
          {data.loading && !data.data ? (
            <Spinner />
          ) : !items.length ? (
            <EmptyState icon="shield" title={tr("Nothing to review")}>
              {tr("Spam and promotions land here instead of becoming tickets. Mark one “Not spam” and it becomes a ticket.")}
            </EmptyState>
          ) : (
            <ul className="vr-spam-list" aria-label={tr("Held email")}>
              {items.map((m) => (
                <li key={m.id} className={openId === m.id ? "is-on" : undefined}>
                  <input type="checkbox" aria-label={tr("Select {v}", { v: m.subject || "email" })} checked={picked.has(m.id)} onChange={() => toggle(m.id)} />
                  <button type="button" className="vr-spam-row" onClick={() => setOpenId(m.id)} aria-current={openId === m.id ? "true" : undefined}>
                    <span className="vr-spam-top">
                      <b>{m.from_name || m.from_addr}</b>
                      <span className="vr-num">{dateTime(m.received_at)}</span>
                    </span>
                    <span className="vr-spam-subject">{m.subject || tr("(no subject)")}</span>
                    <span className="vr-spam-meta">
                      <Badge tone={HELD_REASON[m.status]?.tone ?? "neutral"}>{tr(HELD_REASON[m.status]?.label) ?? m.status}</Badge>
                      <span className="vr-help">{m.snippet}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title={open ? open.subject || tr("(no subject)") : tr("Preview")}>
          {!openId ? (
            <p className="vr-muted" style={{ margin: 0 }}>
              {tr("Choose an email to see why it was held. Its content is shown as data: links don’t open by themselves and images don’t load.")}
            </p>
          ) : detail.loading && !open ? (
            <Spinner />
          ) : open ? (
            <div className="vr-form">
              <dl className="vr-mail-addr">
                <dt>{tr("From")}</dt>
                <dd>{open.from_name ? `${open.from_name} <${open.from_addr}>` : open.from_addr}</dd>
                <dt>{tr("To")}</dt>
                <dd>{open.to.join(", ")}</dd>
                <dt>{tr("Received")}</dt>
                <dd className="vr-mono">{dateTime(open.received_at)}</dd>
              </dl>
              <Banner tone={open.status === "spam" ? "danger" : "warn"} title={tr(HELD_REASON[open.status]?.label) ?? tr("Held")}>
                {heldReason(open)}
              </Banner>
              <MailBody text={open.text ?? ""} html={open.html} title={tr("Held email: {subject}", { subject: open.subject })} />
              {open.attachments?.length ? (
                <div className="vr-atts" aria-label={tr("Attachments")}>
                  {open.attachments.map((a) => (
                    <AttachmentChip key={a.id} a={a} />
                  ))}
                </div>
              ) : null}
              <label className="vr-checkbox-row">
                <input type="checkbox" checked={allow} onChange={(e) => setAllow(e.target.checked)} /> {tr("Always allow mail from")} {open.from_addr}
              </label>
              <div className="vr-actions">
                <Button variant="primary" icon="check" busy={busy === `ok-${open.id}`} onClick={() => notSpam(open)}>
                  {tr("Not spam: make it a ticket")}
                </Button>
                <Button variant="ghost" busy={busy === "delete"} onClick={() => remove([open.id])}>
                  {tr("Delete")}
                </Button>
              </div>
            </div>
          ) : (
            <Banner tone="danger">{detail.error?.message ?? tr("This email isn’t here any more.")}</Banner>
          )}
        </Card>
      </div>
    </>
  );
}
