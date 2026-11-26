"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge, Banner, Button, Card, EmptyState, Field, Icon, PageHeader, Spinner, UsageMeter } from "@/components/ui";
import { api, errorText, type Chatbot } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

export default function Chatbots() {
  const { can } = useSession();
  const router = useRouter();
  const data = useData<{ chatbots: Chatbot[]; domains_used: number; domains_limit: number }>("/chatbots");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const manage = can("chatbots.manage");

  if (data.loading && !data.data) return <Spinner />;
  if (data.error) return <Banner tone="danger">{data.error.message}</Banner>;
  const d = data.data!;

  return (
    <>
      <PageHeader title="Chatbots" subtitle="An assistant on your website that answers from the knowledge base, cites its sources and hands customers to your team when it can’t help." />
      <div className="vr-grid vr-grid-2" style={{ alignItems: "start" }}>
        <Card title="Authorized domains">
          <UsageMeter label="Domains across all chatbots" used={d.domains_used} total={d.domains_limit} />
          <p className="vr-help" style={{ marginTop: 8 }}>
            A chatbot only loads on the domains you authorize. Your plan sets how many you can use.
          </p>
        </Card>
        {manage ? (
          <Card title="New chatbot">
            <form
              className="vr-form-row"
              style={{ gridTemplateColumns: "1fr auto", alignItems: "end" }}
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                setErr(null);
                try {
                  const b = await api.post<Chatbot>("/chatbots", { name });
                  router.push(`/chatbots/${b.id}`);
                } catch (e2) {
                  setErr(errorText(e2));
                  setBusy(false);
                }
              }}
            >
              <Field label="Name" placeholder="Acme Help" value={name} onChange={(e) => setName(e.target.value)} error={err} required />
              <Button type="submit" variant="primary" icon="plus" busy={busy} disabled={!name.trim()}>
                Create
              </Button>
            </form>
          </Card>
        ) : null}
      </div>
      {d.chatbots.length === 0 ? (
        <EmptyState icon="chat" title="No chatbots yet">
          Create one, authorize your website’s domain and paste one line of code into your site.
        </EmptyState>
      ) : (
        <div className="vr-card vr-list">
          {d.chatbots.map((b) => (
            <Link key={b.id} href={`/chatbots/${b.id}`} style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <span className="vr-ws-mark" style={{ background: b.appearance.primary_color, color: "#fff" }}>
                <Icon name="chat" />
              </span>
              <span style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
                <b>{b.name}</b>
                <span className="vr-muted" style={{ fontSize: 13 }}>
                  {b.allowed_domains.length ? b.allowed_domains.join(", ") : "No domains yet: it won’t load anywhere"}
                </span>
              </span>
              <span className="vr-mono vr-muted" style={{ fontSize: 12 }}>
                {b.model}
              </span>
              <Badge tone={b.status === "active" && b.allowed_domains.length ? "brand" : "neutral"} dot>
                {b.status === "paused" ? "Paused" : b.allowed_domains.length ? "Live" : "Not installed"}
              </Badge>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
