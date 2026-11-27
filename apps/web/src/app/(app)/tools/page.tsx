"use client";

import Link from "next/link";

import { CallStatusBadge, ToolStatusBadge } from "@/components/tools";
import { Badge, Banner, Button, Card, dateTime, EmptyState, PageHeader, Spinner, UsageMeter } from "@/components/ui";
import type { ApiTools } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

export default function ToolsPage() {
  const { can } = useSession();
  const manage = can("integrations.manage");
  const data = useData<ApiTools>(manage ? "/tools" : null);
  const header = (
    <PageHeader
      eyebrow="Integrations"
      title="API tools"
      subtitle="Your own APIs, such as order status or stock levels. Chatbots you link them to decide when to call them, and every call shows in the answer’s “How it was answered”."
      actions={
        manage && data.data && (data.data.limit < 0 || data.data.used < data.data.limit) ? (
          <Link href="/tools/new" className="vr-btn vr-btn-primary vr-btn-md">
            New API tool
          </Link>
        ) : null
      }
    />
  );
  if (!manage)
    return (
      <>
        {header}
        <Banner tone="neutral">Your role can’t manage integrations.</Banner>
      </>
    );
  if (data.loading && !data.data) return <Spinner />;
  if (data.error || !data.data) return <Banner tone="danger">{data.error?.message ?? "Couldn’t load API tools."}</Banner>;
  const { items, limit, used } = data.data;
  return (
    <>
      {header}
      <Card title="Tools" actions={<span className="vr-num">{limit < 0 ? `${used} tools` : `${used} of ${limit}`}</span>}>
        <UsageMeter used={used} total={limit} label="API tools on your plan" />
        {!items.length ? (
          <EmptyState
            icon="link"
            title="No API tools yet"
            action={
              <Link href="/tools/new" className="vr-btn vr-btn-primary vr-btn-md">
                New API tool
              </Link>
            }
          >
            Describe an API once (its address, parameters and key) and the chatbot can look up live answers, like where an order is.
          </EmptyState>
        ) : (
          <div className="vr-tools" aria-label="API tools">
            {items.map((t) => (
              <div key={t.id} className="vr-tool-row">
                <span className="vr-tool-main">
                  <Link href={`/tools/${t.id}`} className="vr-row-link">
                    {t.display_name}
                  </Link>
                  <span className="vr-mono vr-muted vr-tool-url">
                    {t.method} {t.url_template}
                  </span>
                  <span className="vr-help">
                    {t.chatbots?.length ? `Used by ${t.chatbots.map((b) => b.name).join(", ")}` : "Not used by a chatbot yet"}
                    {t.last_test_at ? ` · Last tested ${dateTime(t.last_test_at)}` : ""}
                  </span>
                </span>
                <span className="vr-actions">
                  <ToolStatusBadge status={t.status} />
                  <Badge>{t.auth_type === "none" ? "No authentication" : t.auth_type === "bearer" ? "Bearer token" : "API key header"}</Badge>
                  {t.last_test_status ? <CallStatusBadge status={t.last_test_status} /> : <Badge>Not tested</Badge>}
                  <Link href={`/tools/${t.id}`} className="vr-btn vr-btn-ghost vr-btn-sm">
                    Edit
                  </Link>
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
      {limit >= 0 && used >= limit ? (
        <Banner tone="warn" title="Your plan’s API tool limit is reached">
          Delete a tool or upgrade your plan to add another.
        </Banner>
      ) : null}
      <Card title="How tools stay safe">
        <ul className="vr-bullets">
          <li>Keys and tokens are encrypted when saved and never shown again, not even here.</li>
          <li>Verity only calls https addresses on the public internet, connects to the address it checked and follows no redirects.</li>
          <li>Each call has a timeout, a response size limit and a rate limit. Values are checked before the call is made.</li>
          <li>What an API answers is passed to the AI as data, never as instructions.</li>
        </ul>
        <Button variant="ghost" size="sm" onClick={() => data.reload()} icon="refresh">
          Refresh
        </Button>
      </Card>
    </>
  );
}
