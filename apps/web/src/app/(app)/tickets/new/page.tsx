"use client";

import { t as tr } from "@/i18n";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Banner, Button, Card, Field, PageHeader, Select } from "@/components/ui";
import { api, ApiError, errorText, type Ticket, type TicketCategory } from "@/lib/api";
import { PRIORITY, PRIORITY_ORDER } from "@/lib/tickets";
import { useData } from "@/lib/use-data";

export default function NewTicket() {
  const router = useRouter();
  const cats = useData<TicketCategory[]>("/ticket-categories");
  const people = useData<{ id: string; name: string }[]>("/tickets/assignees");
  const teams = useData<{ items: { id: string; name: string }[] }>("/teams");
  const [f, setF] = useState({ subject: "", description: "", requester_name: "", requester_email: "", category_id: "", priority: "normal", assignee_id: "", team_id: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ text: string; field?: string } | null>(null);
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const t = await api.post<Ticket>("/tickets", {
        subject: f.subject,
        description: f.description,
        requester_name: f.requester_name || null,
        requester_email: f.requester_email || null,
        category_id: f.category_id || null,
        priority: f.priority,
        assignee_id: f.assignee_id || null,
        team_id: f.team_id || null,
      });
      router.push(`/tickets/${t.id}`);
    } catch (e2) {
      setErr({ text: errorText(e2), field: e2 instanceof ApiError ? e2.field : undefined });
      setBusy(false);
    }
  }

  return (
    <>
      <div>
        <Link className="vr-link" href="/tickets" style={{ fontSize: 13 }}>
          {tr("← Tickets")}
        </Link>
      </div>
      <PageHeader title={tr("New ticket")} subtitle={tr("For requests that reach you outside the chat, such as a phone call. Email tickets arrive automatically with the email integration.")} />
      {err && !err.field ? <Banner tone="danger">{err.text}</Banner> : null}
      <Card>
        <form className="vr-form" style={{ maxWidth: 640 }} onSubmit={submit}>
          <Field label={tr("Subject")} value={f.subject} onChange={(e) => set("subject")(e.target.value)} required maxLength={200} error={err?.field === "subject" ? err.text : null} />
          <label className="vr-field">
            <span className="vr-field-top">{tr("Description")}</span>
            <span className="vr-input vr-textarea">
              <textarea rows={5} value={f.description} onChange={(e) => set("description")(e.target.value)} />
            </span>
          </label>
          <div className="vr-form-row">
            <Field label={tr("Customer name")} value={f.requester_name} onChange={(e) => set("requester_name")(e.target.value)} />
            <Field label={tr("Customer email")} type="email" value={f.requester_email} onChange={(e) => set("requester_email")(e.target.value)} error={err?.field === "requester_email" ? err.text : null} />
          </div>
          <div className="vr-form-row">
            <Select label={tr("Category")} value={f.category_id} onChange={set("category_id")} options={[{ value: "", label: tr("No category") }, ...(cats.data ?? []).map((c) => ({ value: c.id, label: c.name }))]} />
            <Select label={tr("Priority")} value={f.priority} onChange={set("priority")} options={PRIORITY_ORDER.map((p) => ({ value: p, label: tr(PRIORITY[p].label) }))} />
          </div>
          <Select
            label={tr("Assignee")}
            value={f.assignee_id}
            onChange={set("assignee_id")}
            help={f.assignee_id ? undefined : tr("Left unassigned, routing picks someone when it’s on.")}
            options={[{ value: "", label: tr("Unassigned") }, ...(people.data ?? []).map((p) => ({ value: p.id, label: p.name }))]}
          />
          {teams.data?.items.length ? (
            <Select label={tr("Team")} value={f.team_id} onChange={set("team_id")} options={[{ value: "", label: tr("Let routing decide") }, ...teams.data.items.map((t) => ({ value: t.id, label: t.name }))]} />
          ) : null}
          {err?.field && err.field !== "subject" && err.field !== "requester_email" ? <Banner tone="danger">{err.text}</Banner> : null}
          <div className="vr-actions">
            <Button type="submit" variant="primary" busy={busy} disabled={!f.subject.trim()}>
              {tr("Create ticket")}
            </Button>
            <Button variant="ghost" onClick={() => router.push("/tickets")}>
              {tr("Cancel")}
            </Button>
          </div>
        </form>
      </Card>
    </>
  );
}
