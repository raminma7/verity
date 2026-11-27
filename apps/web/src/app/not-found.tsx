import { t } from "@/i18n";
import Link from "next/link";

export default function NotFound() {
  return (
    <main className="vr-auth-form" style={{ minHeight: "100vh" }}>
      <div className="vr-auth-box">
        <span className="vr-logo">{t("Verity")}</span>
        <div className="vr-auth-title">
          <h1 className="vr-display">{t("Page not found")}</h1>
          <p className="vr-muted">{t("That address doesn’t match any page in Verity.")}</p>
        </div>
        <Link className="vr-link" href="/dashboard">
          {t("← Back to your dashboard")}
        </Link>
      </div>
    </main>
  );
}
