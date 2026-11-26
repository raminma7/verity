"use client";

import { useState } from "react";

import { Banner, Button, Card, Field, Select } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { useSession } from "@/lib/session";

const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "fa", label: "فارسی (Persian)" },
  { value: "de", label: "Deutsch" },
  { value: "fr", label: "Français" },
  { value: "es", label: "Español" },
  { value: "ar", label: "العربية (Arabic)" },
  { value: "tr", label: "Türkçe" },
];

function timezones(current: string) {
  let zones: string[] = [];
  try {
    zones = (Intl as unknown as { supportedValuesOf: (k: string) => string[] }).supportedValuesOf("timeZone");
  } catch {}
  if (!zones.includes("UTC")) zones.unshift("UTC");
  if (!zones.includes(current)) zones.unshift(current);
  return zones.map((z) => ({ value: z, label: z.replace(/_/g, " ") }));
}

export default function Profile() {
  const { user, refresh } = useSession();
  const [name, setName] = useState(user.full_name);
  const [locale, setLocale] = useState(user.locale);
  const [tz, setTz] = useState(user.timezone);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);

  return (
    <Card title="Profile">
      <form
        className="vr-form"
        style={{ maxWidth: 520 }}
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setMsg(null);
          try {
            await api.patch("/me", { full_name: name, locale, timezone: tz });
            await refresh();
            setMsg({ tone: "brand", text: "Profile saved." });
          } catch (err) {
            setMsg({ tone: "danger", text: errorText(err) });
          } finally {
            setBusy(false);
          }
        }}
      >
        {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
        <Field label="Full name" name="full_name" value={name} onChange={(e) => setName(e.target.value)} />
        <Field label="Email" name="email" value={user.email} disabled help="Contact support to change the email on your account." />
        <div className="vr-form-row">
          <Select label="Language" value={locale} onChange={setLocale} options={LANGUAGES} />
          <Select label="Timezone" value={tz} onChange={setTz} options={timezones(user.timezone)} />
        </div>
        <div>
          <Button type="submit" variant="primary" busy={busy}>
            Save profile
          </Button>
        </div>
      </form>
    </Card>
  );
}
