import type { Metadata } from "next";

import { Widget } from "./widget";

export const metadata: Metadata = { title: "Chat", robots: { index: false, follow: false } };

export default async function WidgetPage({ params, searchParams }: { params: Promise<{ key: string }>; searchParams: Promise<{ origin?: string; page?: string }> }) {
  const { key } = await params;
  const sp = await searchParams;
  return <Widget chatKey={key} origin={sp.origin ?? ""} page={sp.page ?? ""} />;
}
