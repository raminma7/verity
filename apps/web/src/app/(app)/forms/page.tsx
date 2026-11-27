"use client";

import { t } from "@/i18n";
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
        title={t("Forms")}
        subtitle={t("Structured requests customers fill in inside the chat. The assistant offers a form when its condition fits the conversation, fills in what the customer already said, and the customer confirms before it becomes a ticket.")}
        actions={
          manage ? (
            <Button variant="primary" icon="plus" onClick={() => router.push("/forms/new")}>
              {t("New form")}
            </Button>
          ) : null
        }
      />
      {data.loading && !data.data ? (
        <Spinner />
      ) : data.error ? (
        <Banner tone="danger">{data.error.message}</Banner>
      ) : !forms.length ? (
        <EmptyState icon="file" title={t("No forms yet")}>
          {t("Create a form for requests that need specific details, like a return or a callback.")}
        </EmptyState>
      ) : (
        <div className="vr-card vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{t("Form")}</th>
                <th>{t("Category")}</th>
                <th>{t("Offered by")}</th>
                <th className="num">{t("Fields")}</th>
                <th>{t("Status")}</th>
                <th className="num">{t("Updated")}</th>
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
                      {f.is_contact ? t("Offered when nobody answers a handoff and no other form fits.") : f.activation_condition || t("Only when the customer opens it.")}
                    </div>
                  </td>
                  <td className="vr-sub">{f.category ?? "—"}</td>
                  <td className="vr-sub">{f.is_contact ? t("Every chatbot") : f.chatbot_ids.length ? t("{length} chatbot{v}", { length: f.chatbot_ids.length, v: f.chatbot_ids.length === 1 ? "" : "s" }) : t("No chatbot")}</td>
                  <td className="num">{f.fields.length}</td>
                  <td>
                    {f.is_contact ? (
                      <Badge tone="brand" dot>
                        {t("Contact form")}
                      </Badge>
                    ) : f.status === "active" ? (
                      <Badge tone="brand" dot>
                        {t("Active")}
                      </Badge>
                    ) : (
                      <Badge dot>{t("Draft")}</Badge>
                    )}
                  </td>
                  <td className="num">
                    {t("v")}{f.version} · {ago(f.updated_at)}
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
