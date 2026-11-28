"use client";

import { t } from "@/i18n";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { DeleteWorkspaceCard, ExportCard, RetentionCard } from "@/components/privacy";
import { Banner, Button, Card, date, Field } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { useSession } from "@/lib/session";

function WorkspaceSettings() {
  const { workspaces, current, can, refresh, switchWorkspace } = useSession();
  const router = useRouter();
  const showNew = useSearchParams().get("new");
  const [name, setName] = useState(current?.workspace.name ?? "");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const editable = can("workspace.update");

  async function act(key: string, fn: () => Promise<void>) {
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

  return (
    <>
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      {showNew ? (
        <Card title={t("Create a new workspace")}>
          <form
            className="vr-form"
            style={{ maxWidth: 520 }}
            onSubmit={(e) => {
              e.preventDefault();
              act("new", async () => {
                const r = await api.post<{ id: string }>("/workspaces", { name: newName });
                await switchWorkspace(r.id);
              });
            }}
          >
            <p className="vr-muted">{t("Each workspace has its own members, plan, knowledge and tickets. You’ll be its Owner.")}</p>
            <Field label={t("Workspace name")} value={newName} onChange={(e) => setNewName(e.target.value)} placeholder={t("Acme Support")} />
            <div className="vr-actions">
              <Button type="submit" variant="primary" busy={busy === "new"} disabled={!newName.trim()}>
                {t("Create workspace")}
              </Button>
              <Button variant="ghost" onClick={() => router.replace("/settings/workspace")}>
                {t("Cancel")}
              </Button>
            </div>
          </form>
        </Card>
      ) : null}
      {current ? (
        <Card title={t("This workspace")}>
          <form
            className="vr-form"
            style={{ maxWidth: 560 }}
            onSubmit={(e) => {
              e.preventDefault();
              act("save", async () => {
                await api.patch("/workspace", { name });
                await refresh();
                setMsg({ tone: "brand", text: t("Workspace saved.") });
              });
            }}
          >
            <Field label={t("Name")} value={name} onChange={(e) => setName(e.target.value)} disabled={!editable} />
            <Field label={t("Workspace ID")} value={current.workspace.slug} disabled className="vr-mono" help={t("Created {created_at}", { created_at: date(current.workspace.created_at) })} />
            <p className="vr-help">
              {t("Ticket settings, such as reopening tickets when customers reply, are under")}{" "}
              <Link className="vr-link" href="/settings/tickets">
                {t("Tickets")}
              </Link>
              .
            </p>
            {editable ? (
              <div className="vr-actions">
                <Button type="submit" variant="primary" busy={busy === "save"}>
                  {t("Save workspace")}
                </Button>
                <Button variant="ghost" icon="plus" onClick={() => router.push("/settings/workspace?new=1")}>
                  {t("New workspace")}
                </Button>
              </div>
            ) : (
              <p className="vr-help">{t("Your role can’t change workspace settings.")}</p>
            )}
          </form>
        </Card>
      ) : null}
      {current ? <RetentionCard /> : null}
      {current && workspaces.find((w) => w.id === current.workspace.id)?.is_owner ? (
        <>
          <ExportCard kind="workspace" />
          <DeleteWorkspaceCard />
        </>
      ) : null}
    </>
  );
}

export default function Page() {
  return (
    <Suspense>
      <WorkspaceSettings />
    </Suspense>
  );
}
