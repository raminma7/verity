import type { MetadataRoute } from "next";

import { siteUrl } from "@/landing/site";

export const dynamic = "force-dynamic";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: ["/"], disallow: ["/api/", "/widget/", "/dashboard", "/admin", "/sign-in", "/sign-up", "/forgot-password", "/reset-password", "/verify-email", "/invite"] }],
    sitemap: `${siteUrl()}/sitemap.xml`,
  };
}
