import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { isLocale } from "@/i18n";
import { PrivacyPage, privacyMetadata } from "@/landing/privacy";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  return isLocale(locale) ? privacyMetadata(locale) : {};
}

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  return <PrivacyPage locale={locale} />;
}
