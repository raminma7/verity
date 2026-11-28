"use client";

import { t } from "@/i18n";
import { useState } from "react";

import { SlaTargets } from "@/components/sla";
import { Badge, Banner, Button, Card, EmptyState, Field, Spinner, Switch } from "@/components/ui";
import { api, errorText, type TicketCategory } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

export default function TicketSettings() {
  const { current, can, refresh } = useSession();
  const cats = useData<TicketCategory[]>("/ticket-categories?include_archived=true");
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<{ id: string; name: string; description: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const [reopen, setReopen] = useState(Boolean(current?.workspace.settings.reopen_on_reply ?? true));
  const configure = can("tickets.configure");
  const list = cats.data ?? [];
  const active = list.filter((c) => !c.archived);
  const archived = list.filter((c) => c.archived);

  async function act(key: string, fn: () => Promise<unknown>, ok?: string) {
    setBusy(key);
    setMsg(null);
    try {
      await fn();
      await cats.reload();
      if (ok) setMsg({ tone: "brand", text: ok });
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }

  function move(i: number, d: -1 | 1) {
    const ids = active.map((c) => c.id);
    [ids[i], ids[i + d]] = [ids[i + d], ids[i]];
    act(`move-${ids[i]}`, () => api.put("/ticket-categories/order", { ids }));
  }

  return (
    <>
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      <Card title={t("Categories")}>
        <p className="vr-muted" style={{ marginTop: 0 }}>
          {t("Every chatbot form sends its tickets to one category. The order here is the order everywhere else.")}
        </p>
        {cats.loading && !cats.data ? (
          <Spinner />
        ) : !list.length ? (
          <EmptyState icon="tag" title={t("No categories yet")}>
            {t("Add one below, or let Verity create “General” the first time a ticket needs one.")}
          </EmptyState>
        ) : (
          <div className="vr-card" aria-label={t("Ticket categories")}>
            {[...active, ...archived].map((c) => {
              const i = active.findIndex((x) => x.id === c.id);
              return (
                <div key={c.id} className={`vr-cat-row${c.archived ? " is-archived" : ""}`}>
                  {editing?.id === c.id ? (
                    <form
                      style={{ flex: 1, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}
                      onSubmit={(e) => {
                        e.preventDefault();
                        act("edit", () => api.patch(`/ticket-categories/${c.id}`, { name: editing.name, description: editing.description }), t("Category saved.")).then(() => setEditing(null));
                      }}
                    >
                      <Field label={t("Name")} value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} maxLength={80} />
                      <Field label={t("Description")} value={editing.description} onChange={(e) => setEditing({ ...editing, description: e.target.value })} maxLength={300} />
                      <Button type="submit" variant="primary" size="sm" busy={busy === "edit"}>
                        {t("Save")}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                        {t("Cancel")}
                      </Button>
                    </form>
                  ) : (
                    <>
                      <span>
                        <b style={{ fontWeight: 500 }}>{c.name}</b>
                        {c.description ? <small className="vr-muted">{c.description}</small> : null}
                      </span>
                      {c.archived ? <Badge>{t("Archived")}</Badge> : <span className="vr-num">{c.open_tickets} {t("active")}</span>}
                      {configure ? (
                        c.archived ? (
                          <Button size="sm" onClick={() => act(`restore-${c.id}`, () => api.post(`/ticket-categories/${c.id}/restore`), t("{name} restored.", { name: c.name }))}>
                            {t("Restore")}
                          </Button>
                        ) : (
                          <span className="vr-actions">
                            <Button size="sm" variant="ghost" icon="chevron" aria-label={t("Move {name} down", { name: c.name })} disabled={i === active.length - 1 || !!busy} onClick={() => move(i, 1)} />
                            <Button size="sm" variant="ghost" aria-label={t("Move {name} up", { name: c.name })} disabled={i === 0 || !!busy} onClick={() => move(i, -1)}>
                              ↑
                            </Button>
                            <Button size="sm" onClick={() => setEditing({ id: c.id, name: c.name, description: c.description })}>
                              {t("Edit")}
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => act(`archive-${c.id}`, () => api.post(`/ticket-categories/${c.id}/archive`), t("{name} archived.", { name: c.name }))}>
                              {t("Archive")}
                            </Button>
                          </span>
                        )
                      ) : null}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {configure ? (
          <form
            className="vr-actions"
            style={{ marginTop: 16, alignItems: "flex-end" }}
            onSubmit={(e) => {
              e.preventDefault();
              act("add", () => api.post("/ticket-categories", { name }), t("{trim} added.", { trim: name.trim() })).then(() => setName(""));
            }}
          >
            <Field label={t("New category")} placeholder={t("Returns")} value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
            <Button type="submit" variant="primary" icon="plus" busy={busy === "add"} disabled={!name.trim()}>
              {t("Add category")}
            </Button>
          </form>
        ) : (
          <p className="vr-help">{t("Your role can’t change categories.")}</p>
        )}
      </Card>
      <Card title={t("When customers reply")}>
        <Switch
          label={t("Reopen resolved tickets when the customer replies")}
          help={t("A resolved ticket goes back to Open when the customer writes again. Pending tickets always move to Open; closed tickets stay closed.")}
          checked={reopen}
          disabled={!can("workspace.update") || busy === "reopen"}
          onChange={(v) => {
            setReopen(v);
            act("reopen", async () => {
              await api.patch("/workspace", { settings: { reopen_on_reply: v } });
              await refresh();
            }, t("Saved."));
          }}
        />
      </Card>
      <SlaTargets />
    </>
  );
}
