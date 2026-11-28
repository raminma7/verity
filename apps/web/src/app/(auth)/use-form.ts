"use client";

import { t } from "@/i18n";
import { useState } from "react";

import { ApiError } from "@/lib/api";

/** Submission state for auth forms: busy flag, a form-level error and per-field errors from the API. */
export function useForm() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      await fn();
    } catch (e) {
      if (e instanceof ApiError && e.field && e.field !== "body") setFieldErrors({ [e.field]: e.message });
      else setError(e instanceof ApiError ? e.message : t("Something went wrong."));
    } finally {
      setBusy(false);
    }
  }

  return { busy, error, fieldErrors, run, setError };
}
