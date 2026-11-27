"use client";

/**
 * A chatbot form as the customer sees it inside the widget. The form builder's preview renders the same
 * component, so what the team designs is what customers get. Built from the design system's tokens:
 * the chatbot's colour (--w-brand) acts, errors use danger and always say how to fix them.
 */
import { useId, type FormEvent } from "react";

import type { FormBlock, FormField, FormValue } from "@/lib/api";

export type ChatFormCopy = {
  optional: string;
  submit: string;
  cancel: string;
  choose: string;
  prefilled: string;
  review: string;
};

export const FORM_COPY_EN: ChatFormCopy = {
  optional: "Optional",
  submit: "Confirm and send",
  cancel: "Not now",
  choose: "Choose…",
  prefilled: "We filled in what you told us in the chat.",
  review: "Check the details, then confirm. Nothing is sent until you do.",
};

export function emptyValue(f: FormField): FormValue {
  return f.type === "multiselect" ? [] : f.type === "checkbox" ? false : "";
}

export function ChatFormFields({
  block,
  values,
  errors = {},
  onChange,
  onSubmit,
  onCancel,
  busy,
  copy = FORM_COPY_EN,
  prefilled,
  readOnly,
}: {
  block: FormBlock;
  values: Record<string, FormValue>;
  errors?: Record<string, string>;
  onChange: (key: string, v: FormValue) => void;
  onSubmit?: () => void;
  onCancel?: () => void;
  busy?: boolean;
  copy?: ChatFormCopy;
  prefilled?: boolean;
  readOnly?: boolean;
}) {
  const uid = useId();
  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit?.();
  }
  return (
    <form className="vr-wform" onSubmit={submit} noValidate aria-label={block.title}>
      <p className="vr-wform-note">
        {prefilled ? `${copy.prefilled} ` : ""}
        {copy.review}
      </p>
      {block.fields.map((f) => {
        const id = `${uid}-${f.key}`;
        const err = errors[f.key];
        const describedBy = [err ? `${id}-err` : null, f.help ? `${id}-help` : null].filter(Boolean).join(" ") || undefined;
        const v = values[f.key] ?? emptyValue(f);
        const common = { id, "aria-invalid": err ? true : undefined, "aria-describedby": describedBy, "aria-required": f.required || undefined, disabled: readOnly || busy };
        return (
          <div key={f.key} className={`vr-wfield${err ? " is-error" : ""}`}>
            {f.type === "checkbox" ? (
              <label className="vr-wcheck" htmlFor={id}>
                <input type="checkbox" checked={v === true} onChange={(e) => onChange(f.key, e.target.checked)} {...common} />
                <span>
                  {f.label}
                  {!f.required ? <small className="vr-wopt"> · {copy.optional}</small> : null}
                </span>
              </label>
            ) : f.type === "multiselect" ? (
              <fieldset className="vr-wfieldset" aria-describedby={describedBy}>
                <legend className="vr-wlabel">
                  {f.label}
                  {!f.required ? <small className="vr-wopt"> · {copy.optional}</small> : null}
                </legend>
                {f.options.map((o) => {
                  const list = Array.isArray(v) ? v : [];
                  return (
                    <label key={o} className="vr-wcheck">
                      <input
                        type="checkbox"
                        checked={list.includes(o)}
                        disabled={readOnly || busy}
                        onChange={(e) => onChange(f.key, e.target.checked ? [...list, o] : list.filter((x) => x !== o))}
                      />
                      <span>{o}</span>
                    </label>
                  );
                })}
              </fieldset>
            ) : (
              <>
                <label className="vr-wlabel" htmlFor={id}>
                  {f.label}
                  {!f.required ? <small className="vr-wopt"> · {copy.optional}</small> : null}
                </label>
                {f.type === "textarea" ? (
                  <textarea
                    className="vr-winput"
                    rows={3}
                    value={String(v ?? "")}
                    placeholder={f.placeholder}
                    maxLength={f.validation.max_length}
                    onChange={(e) => onChange(f.key, e.target.value)}
                    {...common}
                  />
                ) : f.type === "select" ? (
                  <select className="vr-winput" value={String(v ?? "")} onChange={(e) => onChange(f.key, e.target.value)} {...common}>
                    <option value="">{copy.choose}</option>
                    {f.options.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className={`vr-winput${f.type === "number" ? " vr-mono" : ""}`}
                    type={f.type === "email" ? "email" : f.type === "number" ? "number" : f.type === "date" ? "date" : "text"}
                    inputMode={f.type === "number" ? "decimal" : undefined}
                    value={v === null || v === undefined ? "" : String(v)}
                    placeholder={f.placeholder}
                    min={f.type === "number" || f.type === "date" ? (f.validation.min as string | undefined) : undefined}
                    max={f.type === "number" || f.type === "date" ? (f.validation.max as string | undefined) : undefined}
                    step={f.type === "number" ? (f.validation.integer ? 1 : "any") : undefined}
                    maxLength={f.type === "text" ? f.validation.max_length : undefined}
                    onChange={(e) => onChange(f.key, f.type === "number" && e.target.value !== "" ? Number(e.target.value) : e.target.value)}
                    {...common}
                  />
                )}
              </>
            )}
            {f.help ? (
              <span className="vr-whelp" id={`${id}-help`}>
                {f.help}
              </span>
            ) : null}
            {err ? (
              <span className="vr-werr" id={`${id}-err`} role="alert">
                {err}
              </span>
            ) : null}
          </div>
        );
      })}
      {readOnly ? null : (
        <div className="vr-wform-actions">
          <button type="submit" className="vr-wbtn" disabled={busy}>
            {busy ? <span className="vr-spin vr-spin-sm" aria-hidden="true" /> : null}
            {copy.submit}
          </button>
          {onCancel ? (
            <button type="button" className="vr-wbtn-ghost" onClick={onCancel} disabled={busy}>
              {copy.cancel}
            </button>
          ) : null}
        </div>
      )}
    </form>
  );
}
