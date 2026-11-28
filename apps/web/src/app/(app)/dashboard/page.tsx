"use client";

import { t } from "@/i18n";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Badge, Banner, Card, compact, date, EmptyState, Icon, Stat, UsageMeter, type IconName } from "@/components/ui";
import type { Limits, UsageSummary } from "@/lib/api";
import { LIMIT_LABELS, limitText } from "@/lib/limits";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? t("Good morning") : h < 18 ? t("Good afternoon") : t("Good evening");
}

function Step({ done, icon, title, body, href, cta, soon }: { done?: boolean; icon: IconName; title: string; body: string; href?: string; cta?: string; soon?: string }) {
  return (
    <div className="vr-card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span className="vr-ws-mark" style={done ? undefined : { background: "var(--surface-sunken)", color: "var(--ink-muted)" }}>
          <Icon name={done ? "check" : icon} />
        </span>
        {done ? <Badge tone="brand" dot>{t("Done")}</Badge> : soon ? <Badge>{soon}</Badge> : null}
      </div>
      <b style={{ fontWeight: 500, fontSize: 15 }}>{title}</b>
      <span className="vr-muted" style={{ fontSize: 13, lineHeight: "20px" }}>
        {body}
      </span>
      {href && !done ? (
        <Link className="vr-link" href={href} style={{ fontSize: 13, marginTop: "auto" }}>
          {cta} →
        </Link>
      ) : null}
    </div>
  );
}

function Dashboard() {
  const { user, current, can, workspaces } = useSession();
  const welcome = useSearchParams().get("welcome");
  const subscribed = !!current?.subscription?.active;
  const usage = useData<UsageSummary>(subscribed && can("usage.read") ? "/usage/summary" : null);
  const first = user.full_name.split(" ")[0];

  if (!current && workspaces.length === 0)
    return (
      <Card>
        <EmptyState
          icon="building"
          title={t("You’re not in a workspace yet")}
          action={
            <Link className="vr-btn vr-btn-primary vr-btn-md" href="/settings/workspace?new=1">
              {t("Create a workspace")}
            </Link>
          }
        >
          {t("Create one for your team, or open an invitation link from a teammate.")}
        </EmptyState>
      </Card>
    );
  if (!current)
    return (
      <Banner tone="danger" title={t("This workspace is suspended.")}>
        {t("Contact support to restore access, or switch to another workspace. Your account settings are still available.")}
      </Banner>
    );

  const u = usage.data && usage.data.active ? usage.data : null;
  const limits = current.limits;

  return (
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, alignItems: "flex-start" }}>
        <Badge tone={subscribed ? "brand" : "warn"} dot>
          {subscribed ? t("{plan_name} plan · active", { plan_name: current.subscription!.plan_name }) : t("No active plan")}
        </Badge>
        <h1 className="vr-display vr-greet">
          {greeting()}, {first}.
          <br />
          <span className="vr-muted">{subscribed ? t("Here’s {name}.", { name: current.workspace.name }) : t("Let’s get your workspace ready.")}</span>
        </h1>
      </div>

      {welcome ? (
        <Banner title={t("Email verified. Your workspace is ready.")}>{t("You’re its Owner, so you can choose a plan, invite your team and change every setting.")}</Banner>
      ) : null}

      {!subscribed ? (
        <Banner
          tone="warn"
          title={t("Choose a plan to turn on Verity’s AI features.")}
          action={
            can("billing.manage") ? (
              <Link className="vr-btn vr-btn-primary vr-btn-sm" href="/plans">
                {t("Compare plans")}
              </Link>
            ) : null
          }
        >
          {can("billing.manage")
            ? t("Until then, the knowledge base, chatbot, tickets and team invitations stay locked. Settings and billing are open.")
            : t("Ask the workspace owner to choose a plan. Settings stay available.")}
        </Banner>
      ) : current.subscription?.cancel_at_period_end ? (
        <Banner tone="warn" title={t("Your plan ends on {renews_at}.", { renews_at: date(current.subscription.renews_at) })} action={<Link className="vr-btn vr-btn-secondary vr-btn-sm" href="/billing">{t("Manage billing")}</Link>}>
          {t("AI features stop after that date unless you resume the subscription.")}
        </Banner>
      ) : u && !u.ai_available ? (
        <Banner tone="danger" title={t("AI token allowance used up.")} action={<Link className="vr-btn vr-btn-secondary vr-btn-sm" href="/plans">{t("Upgrade")}</Link>}>
          {t("AI answers are paused until {date}. Manual ticketing, email ingestion and forms keep working.", { date: date(u.period_end) })}
        </Banner>
      ) : u && u.percent_used >= 80 ? (
        <Banner tone="warn" title={t("{percent_used}% of this period’s AI tokens used.", { percent_used: Math.floor(u.percent_used) })} action={<Link className="vr-btn vr-btn-secondary vr-btn-sm" href="/usage">{t("See usage")}</Link>}>
          {t("{tokens} weighted tokens remain until {date}.", { tokens: compact(u.remaining), date: date(u.period_end) })}
        </Banner>
      ) : null}

      {subscribed ? (
        <div className="vr-grid vr-grid-4">
          <Stat label={t("Plan")} value={current.subscription!.plan_name} note={current.subscription!.cancel_at_period_end ? t("Ends {renews_at}", { renews_at: date(current.subscription!.renews_at) }) : t("Renews {renews_at}", { renews_at: date(current.subscription!.renews_at) })} />
          <Stat label={t("AI tokens left")} value={u ? compact(u.remaining) : "—"} note={u ? t("{percent_used}% of {total_allowance} used", { percent_used: u.percent_used, total_allowance: compact(u.total_allowance) }) : undefined} />
          <Stat label={t("Members")} value={current.usage.seats} note={limits.members < 0 ? t("No member limit") : t("of {members} seats", { members: limits.members })} />
          <Stat label={t("AI status")} value={current.ai_available ? "On" : "Paused"} note={current.ai_available ? t("Answers and automations run") : t("Allowance exhausted")} />
        </div>
      ) : null}

      <section style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <span className="vr-eyebrow">{t("Set up your workspace")}</span>
        <div className="vr-grid vr-grid-4">
          <Step done={subscribed} icon="card" title={t("Choose a plan")} body={t("Pick the allowance and limits that fit your support volume.")} href="/plans" cta={t("Compare plans")} />
          <Step done={current.usage.seats > 1} icon="users" title={t("Invite your team")} body={t("Add administrators, support agents and viewers.")} href="/members" cta={t("Invite people")} />
          <Step icon="book" title={t("Add knowledge")} body={t("Upload documents and crawl your help centre so answers cite real sources.")} href="/knowledge" cta={t("Add documents or a website")} />
          <Step icon="chat" title={t("Install the chatbot")} body={t("Embed one script on your site and start answering customers.")} href="/chatbots" cta={t("New chatbot")} />
        </div>
      </section>

      <Card title={t("Plan limits")} actions={<Link className="vr-link" style={{ fontSize: 13 }} href="/plans">{subscribed ? t("Change plan") : t("See plans")}</Link>}>
        <div className="vr-grid vr-grid-3" style={{ gap: 20 }}>
          <UsageMeter label={t("Members")} used={current.usage.seats} total={limits.members} />
          {u ? <UsageMeter label={t("AI tokens (weighted)")} used={u.consumed_weighted} total={u.total_allowance} /> : null}
          {(Object.keys(LIMIT_LABELS) as (keyof Limits)[])
            .filter((k) => k !== "members")
            .map((k) => (
              <div key={k} className="vr-usage">
                <div className="vr-usage-top">
                  <span>{t(LIMIT_LABELS[k])}</span>
                  <span className="vr-mono">{limitText(k, limits[k])}</span>
                </div>
              </div>
            ))}
        </div>
      </Card>
    </>
  );
}

export default function Page() {
  return (
    <Suspense>
      <Dashboard />
    </Suspense>
  );
}
