import type { Metadata } from "next";
import Script from "next/script";

import { getTranslator } from "@/i18n/server";
import type { Locale } from "@/i18n";
import { Cited, Closing, Contact, Control, Cost, EmailSection, Faq, Footer, Forms, Header, Hero, How, Loop, More, Pricing, SkipLink, ToolsAndRouting, type Ctx } from "./sections";
import { fetchContactEnabled } from "./contact";
import { fetchPlans } from "./plans";
import { OG_LOCALE, SITE_LOCALES, alternates, pathFor, siteUrl } from "./site";

/** Verity's own chatbot on its public pages: the embed line from the product itself, with the key from VERITY_SITE_CHATBOT_KEY.
 *  Nothing is added when it is unset. The chatbot must authorize this site's address under Chatbots in the dashboard. */
function SiteChatbot() {
  const key = process.env.VERITY_SITE_CHATBOT_KEY?.trim();
  if (!key || !/^vb_[A-Za-z0-9_-]+$/.test(key)) return null;
  return <Script src={`${siteUrl()}/widget.js`} data-verity-key={key} strategy="afterInteractive" />;
}

export async function landingMetadata(locale: Locale): Promise<Metadata> {
  const { t } = await getTranslator(locale);
  const title = t("Verity: one workspace for the whole support loop");
  const description = t("AI customer support whose answers cite your own documents: knowledge base, chatbot, live handoff, tickets and email in one workspace.");
  return {
    metadataBase: new URL(siteUrl()),
    title: { absolute: title },
    description,
    alternates: { canonical: pathFor(locale), languages: alternates() },
    openGraph: { type: "website", siteName: t("Verity"), title, description, url: pathFor(locale), locale: OG_LOCALE[locale], alternateLocale: SITE_LOCALES.filter((l) => l !== locale).map((l) => OG_LOCALE[l]) },
    twitter: { card: "summary_large_image", title, description },
  };
}

export async function LandingPage({ locale }: { locale: Locale }) {
  const tr = await getTranslator(locale);
  const [plans, contact] = await Promise.all([fetchPlans(), fetchContactEnabled()]);
  const c: Ctx = { t: tr.t, tp: tr.tp, locale, dict: tr.dict };
  return (
    <div className="vr-land">
      <SkipLink t={c.t} />
      <Header t={c.t} locale={locale} />
      <main id="main" tabIndex={-1}>
        <Hero {...c} />
        <Loop {...c} />
        <Cited {...c} />
        <Control {...c} />
        <Forms {...c} />
        <EmailSection {...c} />
        <ToolsAndRouting {...c} />
        <Cost {...c} />
        <More {...c} />
        <How {...c} />
        <Pricing {...c} plans={plans} />
        <Faq {...c} />
        {contact ? <Contact {...c} /> : null}
        <Closing {...c} />
      </main>
      <Footer t={c.t} locale={locale} />
      <SiteChatbot />
    </div>
  );
}
