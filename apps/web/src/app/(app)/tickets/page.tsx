"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { PriorityBadge, StatusBadge } from "@/components/tickets";
import { Banner, Button, EmptyState, Field, PageHeader, Segmented, Select, Spinner } from "@/components/ui";
import type { Ticket, TicketCategory, TicketStatus } from "@/lib/api";
import { ago } from "@/lib/knowledge";
import { useSession } from "@/lib/session";
import { PRIORITY, PRIORITY_ORDER, SOURCE, STATUS, STATUS_ORDER } from "@/lib/tickets";
import { useData } from "@/lib/use-data";

export default function TicketsPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <Tickets />
    </Suspense>
  );
}

function Tickets() {
  const params = useSearchParams();
  const router = useRouter();
  const { can } = useSession();
  const [status, setStatus] = useState(params.get("status") ?? "active");
  const [priority, setPriority] = useState("");
  const [category, setCategory] = useState("");
  const [assignee, setAssignee] = useState(params.get("assignee") ?? "");
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setTerm(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const query = new URLSearchParams();
  if (status !== "all") query.set("status", status);
  if (priority) query.set("priority", priority);
  if (category) query.set("category_id", category);
  if (assignee) query.set("assignee", assignee);
  if (term) query.set("q", term);
  const data = useData<{ items: Ticket[]; counts: Record<TicketStatus, number> }>(`/tickets?${query.toString()}`);
  const cats = useData<TicketCategory[]>("/ticket-categories");
  const people = useData<{ id: string; name: string }[]>("/tickets/assignees");
  const { reload } = data;
  useEffect(() => {
    const t = setInterval(reload, 15000);
    return () => clearInterval(t);
  }, [reload]);

  const counts = data.data?.counts;
  const active = counts ? counts.new + counts.open + counts.pending : 0;

  return (
    <>
      <PageHeader
        title="Tickets"
        subtitle="Requests from email, chatbot forms, chat conversations and your team. Work them from New to Resolved; customers who reply reopen them."
        actions={
          can("tickets.manage") ? (
            <Button variant="primary" icon="plus" onClick={() => router.push("/tickets/new")}>
              New ticket
            </Button>
          ) : null
        }
      />
      <Segmented
        label="Status"
        value={status}
        onChange={setStatus}
        options={[
          { value: "active", label: `Active${counts ? ` · ${active}` : ""}` },
          ...STATUS_ORDER.map((s) => ({ value: s, label: `${STATUS[s].label}${counts ? ` · ${counts[s]}` : ""}` })),
          { value: "all", label: "All" },
        ]}
      />
      <div className="vr-filters">
        <div className="vr-grow">
          <Field icon="search" placeholder="Search subject, email, order number or #ticket…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search tickets" />
        </div>
        <Select
          label="Assignee"
          value={assignee}
          onChange={setAssignee}
          options={[{ value: "", label: "Anyone" }, { value: "me", label: "Assigned to me" }, { value: "none", label: "Unassigned" }, ...(people.data ?? []).map((p) => ({ value: p.id, label: p.name }))]}
        />
        <Select label="Priority" value={priority} onChange={setPriority} options={[{ value: "", label: "Any priority" }, ...PRIORITY_ORDER.map((p) => ({ value: p, label: PRIORITY[p].label }))]} />
        <Select label="Category" value={category} onChange={setCategory} options={[{ value: "", label: "All categories" }, ...(cats.data ?? []).map((c) => ({ value: c.id, label: c.name }))]} />
      </div>
      {data.loading && !data.data ? (
        <Spinner />
      ) : data.error ? (
        <Banner tone="danger">{data.error.message}</Banner>
      ) : !data.data?.items.length ? (
        <EmptyState icon="tag" title={term || priority || category || assignee ? "No tickets match" : "No tickets here"}>
          {term || priority || category || assignee
            ? "Try another search or clear a filter."
            : "Tickets appear when customers send a chatbot form, when an agent turns a chat into a ticket, or when your team adds one."}
        </EmptyState>
      ) : (
        <div className="vr-card vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Subject</th>
                <th>Requester</th>
                <th>Category</th>
                <th>Priority</th>
                <th>Status</th>
                <th>Assignee</th>
                <th className="num">Updated</th>
              </tr>
            </thead>
            <tbody>
              {data.data.items.map((t) => (
                <tr key={t.id}>
                  <td className="vr-num">#{t.number}</td>
                  <td>
                    <Link className="vr-row-link" href={`/tickets/${t.id}`}>
                      {t.subject}
                    </Link>
                    <div className="vr-muted" style={{ fontSize: 12 }}>
                      {SOURCE[t.source] ?? t.source}
                      {t.reopened_count ? ` · reopened ${t.reopened_count}×` : ""}
                    </div>
                  </td>
                  <td className="vr-sub">{t.requester_email || t.requester_name || "—"}</td>
                  <td className="vr-sub">{t.category ?? "—"}</td>
                  <td>
                    <PriorityBadge priority={t.priority} />
                  </td>
                  <td>
                    <StatusBadge status={t.status} />
                  </td>
                  <td className="vr-sub">{t.assignee ?? "Unassigned"}</td>
                  <td className="num">{ago(t.last_activity_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
