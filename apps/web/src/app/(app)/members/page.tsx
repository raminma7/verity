"use client";

import { t } from "@/i18n";
import Link from "next/link";
import { useState } from "react";

import { Avatar, Badge, Banner, Button, Card, date, EmptyState, Field, PageHeader, Select, Spinner } from "@/components/ui";
import { api, errorText, type Role } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Member = { id: string; user_id: string; full_name: string; email: string; status: string; role: Role; is_owner: boolean; joined_at: string };
type Invite = { id: string; email: string; role: Role; expires_at: string; created_at: string };

export default function Members() {
  const { user, current, can, refresh } = useSession();
  const data = useData<{ members: Member[]; invitations: Invite[] }>("/workspace/members");
  const roles = useData<Role[]>("/workspace/roles");
  const [email, setEmail] = useState("");
  const [roleId, setRoleId] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);

  const manage = can("members.manage");
  const subscribed = !!current?.subscription?.active;
  const isOwner = current?.role.key === "owner";
  const myRank = current?.role.rank ?? 0;
  const grantable = (roles.data ?? []).filter((r) => r.key !== "owner" && (isOwner || r.rank < myRank));
  const limit = current?.limits.members ?? 0;
  const seats = current?.usage.seats ?? 0;
  const full = limit >= 0 && seats >= limit;

  async function act(key: string, fn: () => Promise<unknown>, ok?: string) {
    setBusy(key);
    setMsg(null);
    try {
      await fn();
      if (ok) setMsg({ tone: "brand", text: ok });
      await Promise.all([data.reload(), refresh()]);
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }

  if (data.loading && !data.data) return <Spinner />;
  if (data.error) return <Banner tone="danger">{data.error.message}</Banner>;

  return (
    <>
      <PageHeader eyebrow={t("Members")} title={t("People and roles")} subtitle={t("{seats} of {limit} seats used, including pending invitations.", { seats: seats, limit: limit < 0 ? "unlimited" : limit })} />
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}

      {manage ? (
        !subscribed ? (
          <Banner tone="warn" title={t("Inviting people needs an active plan.")} action={can("billing.manage") ? <Link className="vr-btn vr-btn-primary vr-btn-sm" href="/plans">{t("See plans")}</Link> : null} />
        ) : (
          <Card title={t("Invite someone")}>
            <form
              className="vr-form-row"
              style={{ gridTemplateColumns: "2fr 1fr auto", alignItems: "end" }}
              onSubmit={(e) => {
                e.preventDefault();
                const rid = roleId || grantable.find((r) => r.key === "agent")?.id || grantable[0]?.id;
                act("invite", () => api.post("/workspace/invitations", { email, role_id: rid }), `Invitation sent to ${email}.`).then(() => setEmail(""));
              }}
            >
              <Field label={t("Email")} type="email" name="invite-email" placeholder={t("teammate@company.com")} value={email} onChange={(e) => setEmail(e.target.value)} disabled={full} />
              <Select
                label={t("Role")}
                value={roleId || grantable.find((r) => r.key === "agent")?.id || ""}
                onChange={setRoleId}
                options={grantable.map((r) => ({ value: r.id, label: r.name }))}
              />
              <Button type="submit" variant="primary" icon="mail" busy={busy === "invite"} disabled={!email || full}>
                {t("Send invite")}
              </Button>
            </form>
            {full ? <p className="vr-help" style={{ marginTop: 8 }}>{t("Your plan’s member limit is reached. Upgrade or remove someone to invite more.")}</p> : null}
          </Card>
        )
      ) : null}

      <Card title={t("Members")} padded={false}>
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{t("Person")}</th>
                <th>{t("Role")}</th>
                <th>{t("Joined")}</th>
                {manage ? <th><span className="sr-only">{t("Actions")}</span></th> : null}
              </tr>
            </thead>
            <tbody>
              {data.data!.members.map((m) => {
                const editable = manage && !m.is_owner && m.user_id !== user.id && (isOwner || m.role.rank < myRank);
                return (
                  <tr key={m.id}>
                    <td>
                      <span className="vr-person">
                        <Avatar name={m.full_name} />
                        <span>
                          <b>
                            {m.full_name}
                            {m.user_id === user.id ? <span className="vr-muted"> {t("(you)")}</span> : null}
                          </b>
                          <small>{m.email}</small>
                        </span>
                      </span>
                    </td>
                    <td>
                      {editable ? (
                        <span style={{ display: "inline-block", minWidth: 200 }}>
                          <Select value={m.role.id} onChange={(v) => act(`role-${m.id}`, () => api.patch(`/workspace/members/${m.id}`, { role_id: v }), `${m.full_name} is now ${roles.data?.find((r) => r.id === v)?.name}.`)} options={grantable.map((r) => ({ value: r.id, label: r.name }))} />
                        </span>
                      ) : (
                        <Badge tone={m.is_owner ? "brand" : "neutral"}>{m.role.name}</Badge>
                      )}
                    </td>
                    <td className="vr-muted">{date(m.joined_at)}</td>
                    {manage ? (
                      <td style={{ textAlign: "right" }}>
                        {editable ? (
                          <Button variant="ghost" size="sm" busy={busy === `rm-${m.id}`} onClick={() => confirm(t("Remove {full_name} from {name}?", { full_name: m.full_name, name: current?.workspace.name })) && act(`rm-${m.id}`, () => api.del(`/workspace/members/${m.id}`), `${m.full_name} was removed.`)}>
                            {t("Remove")}
                          </Button>
                        ) : null}
                        {isOwner && !m.is_owner ? (
                          <Button variant="ghost" size="sm" onClick={() => confirm(t("Make {full_name} the owner? You’ll become a Workspace Administrator.", { full_name: m.full_name })) && act(`own-${m.id}`, () => api.post(`/workspace/transfer-ownership/${m.id}`), "Ownership transferred.")}>
                            {t("Make owner")}
                          </Button>
                        ) : null}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title={t("Pending invitations")} padded={false}>
        {data.data!.invitations.length === 0 ? (
          <EmptyState icon="mail" title={t("No pending invitations")} />
        ) : (
          <div className="vr-table-wrap">
            <table className="vr-table">
              <thead>
                <tr>
                  <th>{t("Email")}</th>
                  <th>{t("Role")}</th>
                  <th>{t("Expires")}</th>
                  {manage ? <th><span className="sr-only">{t("Actions")}</span></th> : null}
                </tr>
              </thead>
              <tbody>
                {data.data!.invitations.map((i) => (
                  <tr key={i.id}>
                    <td>{i.email}</td>
                    <td>
                      <Badge>{i.role.name}</Badge>
                    </td>
                    <td className="vr-muted">{date(i.expires_at)}</td>
                    {manage ? (
                      <td style={{ textAlign: "right" }}>
                        <Button variant="ghost" size="sm" busy={busy === `inv-${i.id}`} onClick={() => act(`inv-${i.id}`, () => api.del(`/workspace/invitations/${i.id}`), "Invitation revoked.")}>
                          {t("Revoke")}
                        </Button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={t("Roles")} padded={false}>
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{t("Role")}</th>
                <th>{t("What it can do")}</th>
              </tr>
            </thead>
            <tbody>
              {(roles.data ?? []).map((r) => (
                <tr key={r.id}>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <b style={{ fontWeight: 500 }}>{r.name}</b>
                    {!r.is_system ? <span className="vr-muted"> {t("· custom")}</span> : null}
                  </td>
                  <td className="vr-muted" style={{ fontSize: 13 }}>
                    {r.description}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      {!isOwner && current?.role ? (
        <div>
          <Button variant="danger" size="sm" onClick={() => confirm(t("Leave {name}?", { name: current.workspace.name })) && act("leave", async () => { await api.post("/workspace/leave"); window.location.href = "/dashboard"; })}>
            {t("Leave workspace")}
          </Button>
        </div>
      ) : null}
    </>
  );
}
