import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { isLocale } from "@/i18n";
import { LandingPage, landingMetadata } from "@/landing/page";
import { SITE_LOCALES } from "@/landing/site";

export function generateStaticParams() {
  return SITE_LOCALES.map((locale) => ({ locale }));
}

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  return isLocale(locale) ? landingMetadata(locale) : {};
}

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  return <LandingPage locale={locale} />;
}
