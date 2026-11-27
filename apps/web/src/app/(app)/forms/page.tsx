"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { Badge, Banner, Button, EmptyState, PageHeader, Spinner } from "@/components/ui";
import type { ChatForm } from "@/lib/api";
import { ago } from "@/lib/knowledge";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

export default function FormsPage() {
  const router = useRouter();
  const { can } = useSession();
  const data = useData<ChatForm[]>("/forms");
  const manage = can("chatbots.manage");
  const forms = data.data ?? [];

  return (
    <>
      <PageHeader
        title="Forms"
        subtitle="Structured requests customers fill in inside the chat. The assistant offers a form when its condition fits the conversation, fills in what the customer already said, and the customer confirms before it becomes a ticket."
        actions={
          manage ? (
            <Button variant="primary" icon="plus" onClick={() => router.push("/forms/new")}>
              New form
            </Button>
          ) : null
        }
      />
      {data.loading && !data.data ? (
        <Spinner />
      ) : data.error ? (
        <Banner tone="danger">{data.error.message}</Banner>
      ) : !forms.length ? (
        <EmptyState icon="file" title="No forms yet">
          Create a form for requests that need specific details, like a return or a callback.
        </EmptyState>
      ) : (
        <div className="vr-card vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>Form</th>
                <th>Category</th>
                <th>Offered by</th>
                <th className="num">Fields</th>
                <th>Status</th>
                <th className="num">Updated</th>
              </tr>
            </thead>
            <tbody>
              {forms.map((f) => (
                <tr key={f.id}>
                  <td>
                    <Link className="vr-row-link" href={`/forms/${f.id}`}>
                      {f.name}
                    </Link>
                    <div className="vr-muted" style={{ fontSize: 12, maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {f.is_contact ? "Offered when nobody answers a handoff and no other form fits." : f.activation_condition || "Only when the customer opens it."}
                    </div>
                  </td>
                  <td className="vr-sub">{f.category ?? "—"}</td>
                  <td className="vr-sub">{f.is_contact ? "Every chatbot" : f.chatbot_ids.length ? `${f.chatbot_ids.length} chatbot${f.chatbot_ids.length === 1 ? "" : "s"}` : "No chatbot"}</td>
                  <td className="num">{f.fields.length}</td>
                  <td>
                    {f.is_contact ? (
                      <Badge tone="brand" dot>
                        Contact form
                      </Badge>
                    ) : f.status === "active" ? (
                      <Badge tone="brand" dot>
                        Active
                      </Badge>
                    ) : (
                      <Badge dot>Draft</Badge>
                    )}
                  </td>
                  <td className="num">
                    v{f.version} · {ago(f.updated_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
