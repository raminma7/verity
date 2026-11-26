import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

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

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {/* eslint-disable-next-line @next/next/no-page-custom-font */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&family=Instrument+Serif:ital@0;1&display=swap"
        />
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
