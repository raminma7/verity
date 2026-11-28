"use client";

import { useParams } from "next/navigation";

import { Banner, Card, PageHeader } from "@/components/ui";
import { t } from "@/i18n";

/** The link from the "export is ready" email: downloads once, for the person who asked, while signed in. */
export default function ExportDownload() {
  const { token } = useParams<{ token: string }>();
  return (
    <>
      <PageHeader eyebrow={t("Data export")} title={t("Your export is ready")} />
      <Card>
        <div className="vr-form" style={{ maxWidth: 560 }}>
          <p className="vr-muted" style={{ margin: 0 }}>
            {t("The ZIP holds JSON files and stored files. The link works once: after the download it can't be used again.")}
          </p>
          <div className="vr-actions">
            <a className="vr-btn vr-btn-primary vr-btn-md" href={`/api/exports/${encodeURIComponent(token)}/download`} download>
              {t("Download the export")}
            </a>
          </div>
          <Banner tone="neutral">{t("If the download says the link was used or has expired, ask for a new export in Settings.")}</Banner>
        </div>
      </Card>
    </>
  );
}
