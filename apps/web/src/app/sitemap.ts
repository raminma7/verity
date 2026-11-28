import type { MetadataRoute } from "next";

import { SITE_LOCALES, alternates, pathFor, siteUrl } from "@/landing/site";

export const dynamic = "force-dynamic"; // the address comes from the environment at run time

export default function sitemap(): MetadataRoute.Sitemap {
  return (["", "/privacy"] as const).flatMap((page) =>
    SITE_LOCALES.map((l) => ({ url: siteUrl() + pathFor(l, page), alternates: { languages: alternates(page) } })),
  );
}
