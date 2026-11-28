import type { Metadata } from "next";

import { Banner } from "@/components/ui";
import type { Locale } from "@/i18n";
import { getTranslator } from "@/i18n/server";
import { Footer, Header, SkipLink } from "./sections";
import { OG_LOCALE, alternates, pathFor, siteUrl } from "./site";

/** The text below describes what the product does, from its own documentation. It is a draft until the company
 *  supplies and approves the wording (who is responsible, how to reach them). While false, the page says so and
 *  is kept out of search results; set it to true once that text is in. */
export const PRIVACY_REVIEWED = false;

export async function privacyMetadata(locale: Locale): Promise<Metadata> {
  const { t } = await getTranslator(locale);
  const title = t("Privacy");
  const description = t("How Verity handles the data in your workspace: retention, export, deletion and the AI providers.");
  return {
    metadataBase: new URL(siteUrl()),
    title: { absolute: `${title} · ${t("Verity")}` },
    description,
    robots: PRIVACY_REVIEWED ? undefined : { index: false, follow: true },
    alternates: { canonical: pathFor(locale, "/privacy"), languages: alternates("/privacy") },
    openGraph: { type: "website", siteName: t("Verity"), title, description, url: pathFor(locale, "/privacy"), locale: OG_LOCALE[locale] },
  };
}

export async function PrivacyPage({ locale }: { locale: Locale }) {
  const { t } = await getTranslator(locale);
  const sections = [
    { h: t("What a workspace holds"), p: t("A workspace holds its knowledge base, chat conversations, tickets and email. Every request is limited to the caller’s own workspace, so one workspace can never read another’s data.") },
    { h: t("How long data is kept"), p: t("Workspace owners choose how long closed tickets, resolved chats, email bodies and attachments, and mail held in Spam are kept. A platform maximum applies, and the default is to keep them until the owner decides otherwise.") },
    { h: t("Exporting your data"), p: t("Owners can export a workspace and anyone can export their own data. A ZIP file is prepared and a link to it is emailed. The link works once and expires after 7 days.") },
    { h: t("Deleting your data"), p: t("Deleting a workspace closes it at once and erases it after 30 days, during which the owner can restore it. Deleting an account signs the person out everywhere and anonymizes it after 30 days unless they sign in again. Invoices are kept for accounting, without the workspace’s name.") },
    { h: t("AI providers"), p: t("Answers are written with Claude and documents are embedded with Voyage, so the text of a question and the passages used to answer it are sent to those providers to produce the answer.") },
    { h: t("Security"), p: t("Two-factor secrets, mailbox passwords and API keys are encrypted, passwords are hashed, and changes that matter are recorded in an audit log.") },
    { h: t("This website"), p: t("If you use the contact form on the landing page, we email your name, address and message to our team and use them only to reply to you.") },
  ];
  return (
    <div className="vr-land">
      <SkipLink t={t} />
      <Header t={t} locale={locale} />
      <main id="main" tabIndex={-1} className="vr-land-section">
        <article className="vr-land-wrap vr-land-legal">
          <h1 className="vr-display vr-land-h1">{t("Privacy")}</h1>
          {PRIVACY_REVIEWED ? null : <Banner tone="warn" title={t("Draft")}>{t("This text is a draft and is waiting for legal review. The company’s details and contact address will be added before launch.")}</Banner>}
          {sections.map((s) => (
            <section key={s.h} aria-label={s.h}>
              <h2 className="vr-h2">{s.h}</h2>
              <p>{s.p}</p>
            </section>
          ))}
        </article>
      </main>
      <Footer t={t} locale={locale} />
    </div>
  );
}
