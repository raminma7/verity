"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Banner, Button, Card, date, Field, Switch } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { useSession } from "@/lib/session";

function WorkspaceSettings() {
  const { current, can, refresh, switchWorkspace } = useSession();
  const router = useRouter();
  const showNew = useSearchParams().get("new");
  const [name, setName] = useState(current?.workspace.name ?? "");
  const [reopen, setReopen] = useState(Boolean(current?.workspace.settings.reopen_on_reply ?? true));
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
        <Card title="Create a new workspace">
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
            <p className="vr-muted">Each workspace has its own members, plan, knowledge and tickets. You’ll be its Owner.</p>
            <Field label="Workspace name" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Acme Support" />
            <div className="vr-actions">
              <Button type="submit" variant="primary" busy={busy === "new"} disabled={!newName.trim()}>
                Create workspace
              </Button>
              <Button variant="ghost" onClick={() => router.replace("/settings/workspace")}>
                Cancel
              </Button>
            </div>
          </form>
        </Card>
      ) : null}
      {current ? (
        <Card title="This workspace">
          <form
            className="vr-form"
            style={{ maxWidth: 560 }}
            onSubmit={(e) => {
              e.preventDefault();
              act("save", async () => {
                await api.patch("/workspace", { name, settings: { reopen_on_reply: reopen } });
                await refresh();
                setMsg({ tone: "brand", text: "Workspace saved." });
              });
            }}
          >
            <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} disabled={!editable} />
            <Field label="Workspace ID" value={current.workspace.slug} disabled className="vr-mono" help={`Created ${date(current.workspace.created_at)}`} />
            <Switch label="Reopen tickets when customers reply" help="A resolved or closed ticket goes back to Open when the customer writes again." checked={reopen} onChange={setReopen} disabled={!editable} />
            {editable ? (
              <div className="vr-actions">
                <Button type="submit" variant="primary" busy={busy === "save"}>
                  Save workspace
                </Button>
                <Button variant="ghost" icon="plus" onClick={() => router.push("/settings/workspace?new=1")}>
                  New workspace
                </Button>
              </div>
            ) : (
              <p className="vr-help">Your role can’t change workspace settings.</p>
            )}
          </form>
        </Card>
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
