"use client";

import { t as tr } from "@/i18n";
import { useEffect, useState } from "react";

import { Badge, Banner, Button, Card, EmptyState, Field, Segmented, Select, Spinner, Switch } from "@/components/ui";
import { api, errorText, type MemberRouting, type RoutingRule, type RoutingSettings, type Team, type TicketCategory } from "@/lib/api";
import { useSession } from "@/lib/session";
import { PRIORITY, PRIORITY_ORDER, SOURCE } from "@/lib/tickets";
import { useData } from "@/lib/use-data";

/* Routing isn't in the design system yet: built from its tokens and rules (app.css, Phase 5A). */

const SOURCES = ["email", "chatbot_form", "conversation", "manual"] as const;

function Chips<T extends string>({ label, options, value, onChange, disabled }: { label: string; options: { value: T; label: string }[]; value: T[]; onChange: (v: T[]) => void; disabled?: boolean }) {
  return (
    <div className="vr-field">
      <span className="vr-field-top">{label}</span>
      <div className="vr-chips" role="group" aria-label={label}>
        {options.map((o) => {
          const on = value.includes(o.value);
          return (
            <button
              key={o.value}
              type="button"
              className={on ? "vr-chip is-on" : "vr-chip"}
              aria-pressed={on}
              disabled={disabled}
              onClick={() => onChange(on ? value.filter((v) => v !== o.value) : [...value, o.value])}
            >
              {o.label}
            </button>
          );
        })}
        {!options.length ? <span className="vr-help">{tr("None yet.")}</span> : null}
      </div>
    </div>
  );
}

function Availability({ value }: { value: "available" | "away" }) {
  return (
    <span className="vr-avail">
      <span className={value === "away" ? "vr-avail-dot is-away" : "vr-avail-dot"} aria-hidden="true" />
      {value === "away" ? tr("Away") : tr("Available")}
    </span>
  );
}

const split = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

const EMPTY_RULE = { name: "", sources: [] as string[], category_ids: [] as string[], priorities: [] as string[], keywords: "", domains: "", target: "team", team_id: "", assignee_id: "" };

export default function RoutingSettingsPage() {
  const { can } = useSession();
  const configure = can("tickets.configure");
  const settings = useData<RoutingSettings>(configure ? "/routing/settings" : null);
  const teams = useData<{ items: Team[] }>("/teams");
  const rules = useData<{ items: RoutingRule[] }>(configure ? "/routing/rules" : null);
  const members = useData<{ items: MemberRouting[] }>("/routing/members");
  const cats = useData<TicketCategory[]>("/ticket-categories");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const [cfg, setCfg] = useState<RoutingSettings | null>(null);
  const [newTeam, setNewTeam] = useState("");
  const [rule, setRule] = useState({ ...EMPTY_RULE });
  const [caps, setCaps] = useState<Record<string, string>>({});

  useEffect(() => {
    if (settings.data) setCfg(settings.data);
  }, [settings.data]);

  const catOpts = (cats.data ?? []).map((c) => ({ value: c.id, label: c.name }));
  const catName = (id: string) => cats.data?.find((c) => c.id === id)?.name ?? "a deleted category";
  const teamList = teams.data?.items ?? [];
  const people = members.data?.items ?? [];
  const personName = (id?: string) => people.find((p) => p.user_id === id)?.name ?? "someone who can’t take tickets";
  const teamName = (id?: string) => teamList.find((t) => t.id === id)?.name ?? "a deleted team";

  async function act(key: string, fn: () => Promise<unknown>, ok?: string) {
    setBusy(key);
    setMsg(null);
    try {
      await fn();
      await Promise.all([teams.reload(), members.reload(), configure ? rules.reload() : null, configure ? settings.reload() : null]);
      if (ok) setMsg({ tone: "brand", text: ok });
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }

  function move(i: number, d: -1 | 1) {
    const ids = (rules.data?.items ?? []).map((r) => r.id);
    [ids[i], ids[i + d]] = [ids[i + d], ids[i]];
    act(`move-${ids[i]}`, () => api.put("/routing/rules/order", { ids }));
  }

  function describe(r: RoutingRule) {
    const c = r.conditions;
    const parts = [
      c.sources?.length ? `from ${c.sources.map((s) => SOURCE[s as keyof typeof SOURCE] ?? s).join(" or ")}` : "",
      c.category_ids?.length ? `in ${c.category_ids.map(catName).join(" or ")}` : "",
      c.priorities?.length ? `${c.priorities.map((p) => PRIORITY[p as keyof typeof PRIORITY]?.label ?? p).join(" or ")} priority` : "",
      c.keywords?.length ? `mentioning ${c.keywords.map((k) => `“${k}”`).join(" or ")}` : "",
      c.requester_domains?.length ? `from @${c.requester_domains.join(", @")}` : "",
    ].filter(Boolean);
    const to = r.action.team_id ? `team “${teamName(r.action.team_id)}”` : personName(r.action.assignee_id);
    return `${parts.length ? `Tickets ${parts.join(", ")}` : "Every ticket"} → ${to}`;
  }

  if (teams.loading && !teams.data) return <Spinner />;

  return (
    <>
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      {!configure ? (
        <Banner tone="neutral">{tr("Owners and administrators set up teams and rules. You can mark yourself away from the menu under your name.")}</Banner>
      ) : null}

      {configure && cfg ? (
        <Card title={tr("Automatic routing")}>
          <Switch
            label={tr("Assign new tickets automatically")}
            help={tr("Email, chatbot forms, chats and tickets your team adds go to the right team, then to its least busy available member. No AI tokens are used.")}
            checked={cfg.enabled}
            onChange={(v) => setCfg({ ...cfg, enabled: v })}
          />
          <div className="vr-actions" style={{ alignItems: "flex-end", marginTop: 12 }}>
            <Select
              label={tr("When nothing else matches")}
              value={cfg.default_team_id ?? ""}
              onChange={(v) => setCfg({ ...cfg, default_team_id: v || null })}
              options={[{ value: "", label: tr("Leave the ticket unassigned") }, ...teamList.map((t) => ({ value: t.id, label: tr("Send to {name}", { name: t.name }) }))]}
            />
            <Button variant="primary" busy={busy === "settings"} onClick={() => act("settings", () => api.put("/routing/settings", cfg), "Routing settings saved.")}>
              {tr("Save")}
            </Button>
          </div>
          <p className="vr-help">
            {tr("Order of checks: your rules from the top, then the team that handles the ticket’s category, then the team above. Inside a team, members who handle the category come first, then whoever has the fewest open tickets. Away members and members at their cap are skipped.")}
          </p>
        </Card>
      ) : null}

      <Card title={tr("Teams")}>
        {!teamList.length ? (
          <EmptyState icon="users" title={tr("No teams yet")}>
            {tr("A team is a group of people who work the same tickets, such as Billing or Shipping.")}
          </EmptyState>
        ) : (
          <div className="vr-card" aria-label={tr("Teams")}>
            {teamList.map((t) => (
              <div key={t.id} className="vr-team">
                <div className="vr-actions" style={{ justifyContent: "space-between" }}>
                  <b>{t.name}</b>
                  <span className="vr-actions">
                    <Badge>{`${t.members.length} ${t.members.length === 1 ? "member" : "members"}`}</Badge>
                    {configure ? (
                      <Button size="sm" variant="ghost" busy={busy === `del-${t.id}`} onClick={() => act(`del-${t.id}`, () => api.del(`/teams/${t.id}`), `${t.name} deleted.`)}>
                        {tr("Delete")}
                      </Button>
                    ) : null}
                  </span>
                </div>
                <Chips
                  label={tr("Members")}
                  disabled={!configure || busy === `m-${t.id}`}
                  options={people.map((p) => ({ value: p.user_id, label: p.name }))}
                  value={t.members.map((m) => m.id)}
                  onChange={(ids) => act(`m-${t.id}`, () => api.put(`/teams/${t.id}/members`, { member_ids: ids }))}
                />
                <Chips
                  label={tr("Handles these categories")}
                  disabled={!configure || busy === `c-${t.id}`}
                  options={catOpts}
                  value={t.category_ids}
                  onChange={(ids) => act(`c-${t.id}`, () => api.patch(`/teams/${t.id}`, { category_ids: ids }))}
                />
              </div>
            ))}
          </div>
        )}
        {configure ? (
          <form
            className="vr-actions vr-team-add"
            style={{ alignItems: "flex-end", marginTop: 12 }}
            onSubmit={(e) => {
              e.preventDefault();
              act("team", () => api.post("/teams", { name: newTeam }), `${newTeam} added. Add its members and categories above.`).then(() => setNewTeam(""));
            }}
          >
            <Field label={tr("New team")} value={newTeam} onChange={(e) => setNewTeam(e.target.value)} placeholder={tr("Billing")} maxLength={80} />
            <Button type="submit" icon="plus" busy={busy === "team"} disabled={!newTeam.trim()}>
              {tr("Add team")}
            </Button>
          </form>
        ) : null}
      </Card>

      {configure ? (
        <Card title={tr("Rules")}>
          <p className="vr-muted" style={{ marginTop: 0 }}>
            {tr("Checked from the top; the first rule whose conditions all match decides. Leave a condition empty to match anything.")}
          </p>
          {rules.data?.items.length ? (
            <div className="vr-rules" aria-label={tr("Routing rules")}>
              {rules.data.items.map((r, i, all) => (
                <div key={r.id} className={r.enabled ? "vr-rule" : "vr-rule is-off"}>
                  <span className="vr-rule-pos">{i + 1}</span>
                  <span className="vr-rule-main">
                    <b>{r.name}</b>
                    <span className="vr-help">{describe(r)}</span>
                  </span>
                  <span className="vr-actions">
                    <Badge tone={r.enabled ? "brand" : "neutral"} dot>
                      {r.enabled ? tr("On") : tr("Off")}
                    </Badge>
                    <Button size="sm" variant="ghost" disabled={i === 0} onClick={() => move(i, -1)} aria-label={tr("Move {name} up", { name: r.name })}>
                      ↑
                    </Button>
                    <Button size="sm" variant="ghost" disabled={i === all.length - 1} onClick={() => move(i, 1)} aria-label={tr("Move {name} down", { name: r.name })}>
                      ↓
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => act(`t-${r.id}`, () => api.patch(`/routing/rules/${r.id}`, { enabled: !r.enabled }))}>
                      {r.enabled ? tr("Turn off") : tr("Turn on")}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => act(`d-${r.id}`, () => api.del(`/routing/rules/${r.id}`), "Rule deleted.")}>
                      {tr("Delete")}
                    </Button>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <span className="vr-help">{tr("No rules: tickets go by category and the default team.")}</span>
          )}
          <form
            className="vr-form"
            style={{ marginTop: 16 }}
            aria-label={tr("Add a rule")}
            onSubmit={(e) => {
              e.preventDefault();
              act(
                "rule",
                () =>
                  api.post("/routing/rules", {
                    name: rule.name,
                    conditions: {
                      sources: rule.sources,
                      category_ids: rule.category_ids,
                      priorities: rule.priorities,
                      keywords: split(rule.keywords),
                      requester_domains: split(rule.domains),
                    },
                    action: rule.target === "team" ? { team_id: rule.team_id || null } : { assignee_id: rule.assignee_id || null },
                  }),
                "Rule added.",
              ).then(() => setRule({ ...EMPTY_RULE }));
            }}
          >
            <b>{tr("Add a rule")}</b>
            <Field label={tr("Name")} value={rule.name} onChange={(e) => setRule({ ...rule, name: e.target.value })} placeholder={tr("Billing email")} maxLength={80} required />
            <Chips label={tr("Source")} options={SOURCES.map((s) => ({ value: s, label: tr(SOURCE[s]) }))} value={rule.sources} onChange={(v) => setRule({ ...rule, sources: v })} />
            <Chips label={tr("Category")} options={catOpts} value={rule.category_ids} onChange={(v) => setRule({ ...rule, category_ids: v })} />
            <Chips label={tr("Priority")} options={PRIORITY_ORDER.map((p) => ({ value: p, label: tr(PRIORITY[p].label) }))} value={rule.priorities} onChange={(v) => setRule({ ...rule, priorities: v })} />
            <div className="vr-grid2">
              <Field label={tr("Words in the subject or message")} value={rule.keywords} onChange={(e) => setRule({ ...rule, keywords: e.target.value })} placeholder={tr("refund, invoice")} help={tr("Separate with commas; any one matches.")} />
              <Field label={tr("Requester’s email domain")} value={rule.domains} onChange={(e) => setRule({ ...rule, domains: e.target.value })} placeholder="bigcorp.com" help={tr("Subdomains match too.")} />
            </div>
            <div className="vr-actions" style={{ alignItems: "flex-end" }}>
              <Segmented
                label={tr("Send to")}
                value={rule.target}
                onChange={(v) => setRule({ ...rule, target: v })}
                options={[
                  { value: "team", label: tr("A team") },
                  { value: "person", label: tr("One person") },
                ]}
              />
              {rule.target === "team" ? (
                <Select label={tr("Team")} value={rule.team_id} onChange={(v) => setRule({ ...rule, team_id: v })} options={[{ value: "", label: tr("Choose a team") }, ...teamList.map((t) => ({ value: t.id, label: t.name }))]} />
              ) : (
                <Select label={tr("Person")} value={rule.assignee_id} onChange={(v) => setRule({ ...rule, assignee_id: v })} options={[{ value: "", label: tr("Choose a person") }, ...people.map((p) => ({ value: p.user_id, label: p.name }))]} />
              )}
              <Button type="submit" variant="primary" busy={busy === "rule"} disabled={!rule.name.trim() || !(rule.target === "team" ? rule.team_id : rule.assignee_id)}>
                {tr("Add rule")}
              </Button>
            </div>
          </form>
        </Card>
      ) : null}

      <Card title={tr("People")} padded={false}>
        {people.length ? (
          <table className="vr-table" aria-label={tr("People who can take tickets")}>
            <thead>
              <tr>
                <th>{tr("Name")}</th>
                <th>{tr("Status")}</th>
                <th className="num">{tr("Open tickets")}</th>
                <th>{tr("Cap")}</th>
                <th>{tr("Handles first")}</th>
                <th>{tr("Teams")}</th>
              </tr>
            </thead>
            <tbody>
              {people.map((p) => (
                <tr key={p.user_id}>
                  <td>
                    {p.name}
                    <div className="vr-muted" style={{ fontSize: 12 }}>
                      {p.email}
                    </div>
                  </td>
                  <td>
                    <span className="vr-actions">
                      <Availability value={p.availability} />
                      {configure ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => act(`a-${p.user_id}`, () => api.put(`/routing/members/${p.user_id}`, { availability: p.availability === "away" ? "available" : "away" }))}
                        >
                          {p.availability === "away" ? tr("Mark available") : tr("Mark away")}
                        </Button>
                      ) : null}
                    </span>
                  </td>
                  <td className="num">{p.open_tickets}</td>
                  <td>
                    {configure ? (
                      <form
                        className="vr-actions"
                        onSubmit={(e) => {
                          e.preventDefault();
                          const v = caps[p.user_id] ?? "";
                          act(`cap-${p.user_id}`, () => api.put(`/routing/members/${p.user_id}`, v ? { max_open: Number(v) } : { clear_max_open: true }), "Cap saved.");
                        }}
                      >
                        <Field
                          aria-label={tr("Most open tickets for {name}", { name: p.name })}
                          type="number"
                          min={1}
                          max={1000}
                          placeholder={tr("No cap")}
                          value={caps[p.user_id] ?? (p.max_open ? String(p.max_open) : "")}
                          onChange={(e) => setCaps({ ...caps, [p.user_id]: e.target.value })}
                          style={{ width: 80 }}
                        />
                        <Button size="sm" type="submit" variant="ghost">
                          {tr("Save")}
                        </Button>
                      </form>
                    ) : (
                      <span className="vr-num">{p.max_open ?? tr("No cap")}</span>
                    )}
                  </td>
                  <td>
                    {configure ? (
                      <Select
                        ariaLabel={`Add a category ${p.name} handles first`}
                        value=""
                        onChange={(v) => v && act(`s-${p.user_id}`, () => api.put(`/routing/members/${p.user_id}`, { skills: [...p.skills, v] }))}
                        options={[{ value: "", label: p.skills.length ? p.skills.map(catName).join(", ") : tr("Any category") }, ...catOpts.filter((c) => !p.skills.includes(c.value)).map((c) => ({ value: c.value, label: `+ ${c.label}` }))]}
                      />
                    ) : (
                      <span className="vr-sub">{p.skills.length ? p.skills.map(catName).join(", ") : tr("Any category")}</span>
                    )}
                    {configure && p.skills.length ? (
                      <Button size="sm" variant="ghost" onClick={() => act(`s-${p.user_id}`, () => api.put(`/routing/members/${p.user_id}`, { skills: [] }))}>
                        {tr("Clear")}
                      </Button>
                    ) : null}
                  </td>
                  <td className="vr-sub">{p.teams.map((t) => t.name).join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="vr-panel-body">
            <span className="vr-help">{tr("Nobody can take tickets yet: invite a Support Agent under Members.")}</span>
          </div>
        )}
      </Card>
    </>
  );
}
