import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import type { ReactNode } from "react";

import { isLocale, RTL } from "@/i18n";

import "@/design/tokens.css";
import "@/design/verity.css";
import "@/design/app.css";

export const metadata: Metadata = {
  title: { default: "Verity", template: "%s · Verity" },
  description: "AI customer support: knowledge base, chatbot and ticketing in one workspace.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f5f3ed" },
    { media: "(prefers-color-scheme: dark)", color: "#0e1012" },
  ],
};

// Applies the saved theme before paint so there's no flash.
const themeScript = `try{var t=localStorage.getItem("verity.theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

/** The public pages are rendered per language on the server: middleware passes it on in a header. The dashboard
 *  sets its own language in the browser, as before. */
export default async function RootLayout({ children }: { children: ReactNode }) {
  const wanted = (await headers()).get("x-verity-locale");
  const locale = isLocale(wanted) ? wanted : "en";
  return (
    <html lang={locale} dir={RTL.has(locale) ? "rtl" : "ltr"} suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {/* eslint-disable-next-line @next/next/no-page-custom-font */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap"
        />
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
