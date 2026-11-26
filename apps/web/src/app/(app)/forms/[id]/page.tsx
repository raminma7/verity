"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ChatFormFields, emptyValue } from "@/components/chat-form";
import { Badge, Banner, Button, Card, Field, PageHeader, Segmented, Select, Spinner, Switch } from "@/components/ui";
import { api, ApiError, errorText, type Chatbot, type ChatForm, type FieldType, type FormField, type FormValue, type TicketCategory } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

const TYPES: { value: FieldType; label: string }[] = [
  { value: "text", label: "Short text" },
  { value: "email", label: "Email" },
  { value: "number", label: "Number" },
  { value: "select", label: "Choose one" },
  { value: "multiselect", label: "Choose several" },
  { value: "date", label: "Date" },
  { value: "textarea", label: "Long text" },
  { value: "checkbox", label: "Checkbox" },
];
const TYPE_LABEL = Object.fromEntries(TYPES.map((t) => [t.value, t.label]));

type Draft = Pick<ChatForm, "name" | "description" | "category_id" | "activation_condition" | "status" | "fields" | "submit_label" | "success_message" | "chatbot_ids"> & {
  is_contact?: boolean;
  version?: number;
};

const BLANK: Draft = { name: "", description: "", category_id: "", activation_condition: "", status: "draft", fields: [], submit_label: "Send", success_message: "", chatbot_ids: [] };

function slug(label: string, taken: string[]) {
  let base = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 36) || "field";
  if (!/^[a-z]/.test(base)) base = `f_${base}`;
  let k = base;
  for (let i = 2; taken.includes(k); i++) k = `${base}_${i}`;
  return k;
}

export default function FormBuilder() {
  const { id } = useParams<{ id: string }>();
  const isNew = id === "new";
  const router = useRouter();
  const { can } = useSession();
  const manage = can("chatbots.manage");
  const loaded = useData<ChatForm>(isNew ? null : `/forms/${id}`);
  const cats = useData<TicketCategory[]>("/ticket-categories");
  const bots = useData<Chatbot[]>("/chatbots");
  const [f, setF] = useState<Draft | null>(isNew ? BLANK : null);
  const [sel, setSel] = useState(0);
  const [preview, setPreview] = useState<Record<string, FormValue>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);

  useEffect(() => {
    if (loaded.data) setF(loaded.data);
  }, [loaded.data]);
  useEffect(() => {
    if (isNew && cats.data?.length && f && !f.category_id) setF({ ...f, category_id: cats.data[0].id });
  }, [isNew, cats.data, f]);

  if (!f) return loaded.error ? <Banner tone="danger">{loaded.error.message}</Banner> : <Spinner />;
  const field = f.fields[sel];
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setF({ ...f, [k]: v });
  const setField = (patch: Partial<FormField>) => set("fields", f.fields.map((x, i) => (i === sel ? { ...x, ...patch } : x)));
  const setRule = (k: keyof FormField["validation"], v: string | number | boolean | undefined) => {
    const rules = { ...field.validation };
    if (v === "" || v === undefined || v === false) delete rules[k];
    else (rules as Record<string, unknown>)[k] = v;
    setField({ validation: rules });
  };

  function addField(type: FieldType) {
    const label = TYPE_LABEL[type];
    const nf: FormField = { key: slug(label, f!.fields.map((x) => x.key)), label, type, required: false, help: "", placeholder: "", options: type === "select" || type === "multiselect" ? ["Option 1", "Option 2"] : [], validation: {} };
    set("fields", [...f!.fields, nf]);
    setSel(f!.fields.length);
  }
  function move(i: number, d: -1 | 1) {
    const list = [...f!.fields];
    [list[i], list[i + d]] = [list[i + d], list[i]];
    set("fields", list);
    setSel(i + d);
  }

  async function save() {
    setBusy(true);
    setMsg(null);
    const body = {
      name: f!.name,
      description: f!.description,
      category_id: f!.category_id,
      activation_condition: f!.activation_condition,
      status: f!.status === "archived" ? undefined : f!.status,
      fields: f!.fields,
      submit_label: f!.submit_label,
      success_message: f!.success_message,
      ...(f!.is_contact ? {} : { chatbot_ids: f!.chatbot_ids }),
    };
    try {
      if (isNew) {
        const created = await api.post<ChatForm>("/forms", body);
        router.replace(`/forms/${created.id}`);
      } else {
        const saved = await api.patch<ChatForm>(`/forms/${id}`, body);
        setF(saved);
        setMsg({ tone: "brand", text: `Saved · version ${saved.version}.` });
      }
    } catch (e) {
      const key = e instanceof ApiError ? (e.data.field_key as string | undefined) : undefined;
      if (key) setSel(Math.max(0, f!.fields.findIndex((x) => x.key === key)));
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  }

  const ro = !manage;
  return (
    <>
      <div>
        <Link className="vr-link" href="/forms" style={{ fontSize: 13 }}>
          ← Forms
        </Link>
      </div>
      <PageHeader
        title={isNew ? "New form" : f.name || "Untitled form"}
        subtitle={
          isNew ? (
            "Each form sends its tickets to one category."
          ) : (
            <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
              {f.is_contact ? <Badge tone="brand" dot>Contact form</Badge> : f.status === "active" ? <Badge tone="brand" dot>Active</Badge> : f.status === "archived" ? <Badge>Archived</Badge> : <Badge dot>Draft</Badge>}
              <span className="vr-num">version {f.version}</span>
            </span>
          )
        }
        actions={
          manage ? (
            <>
              {!isNew && !f.is_contact && f.status !== "archived" ? (
                <Button
                  variant="ghost"
                  onClick={async () => {
                    if (!confirm(`Archive ${f.name}? Chatbots stop offering it. Tickets it created stay.`)) return;
                    await api.post(`/forms/${id}/archive`);
                    router.push("/forms");
                  }}
                >
                  Archive
                </Button>
              ) : null}
              <Button variant="primary" busy={busy} onClick={save} disabled={!f.name.trim() || !f.category_id || f.status === "archived"}>
                {isNew ? "Create form" : "Save form"}
              </Button>
            </>
          ) : null
        }
      />
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      {cats.data && !cats.data.length ? (
        <Banner tone="warn" title="Add a ticket category first">
          Forms send their tickets to a category. <Link className="vr-link" href="/settings/tickets">Add one in Settings → Tickets</Link>.
        </Banner>
      ) : null}
      <div className="vr-builder">
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
          <Card title="Details">
            <div className="vr-form">
              <Field label="Name" value={f.name} onChange={(e) => set("name", e.target.value)} maxLength={80} disabled={ro} placeholder="Return request" />
              <Field label="Description" help="Shown to the customer above the form." value={f.description} onChange={(e) => set("description", e.target.value)} maxLength={500} disabled={ro} />
              <div className="vr-form-row">
                <Select label="Ticket category" value={f.category_id} disabled={ro} onChange={(v) => set("category_id", v)} options={[...(f.category_id ? [] : [{ value: "", label: "Choose…" }]), ...(cats.data ?? []).map((c) => ({ value: c.id, label: c.name }))]} />
                <Field label="Button label" value={f.submit_label} onChange={(e) => set("submit_label", e.target.value)} maxLength={40} disabled={ro} />
              </div>
              {!f.is_contact ? (
                <div>
                  <span className="vr-field-top" style={{ display: "block", marginBottom: 6 }}>
                    Status
                  </span>
                  <Segmented
                    label="Status"
                    value={f.status === "archived" ? "draft" : f.status}
                    onChange={(v) => !ro && set("status", v as Draft["status"])}
                    options={[
                      { value: "draft", label: "Draft" },
                      { value: "active", label: "Active" },
                    ]}
                  />
                </div>
              ) : null}
              <Field
                label="Message after sending"
                help="Leave empty to tell the customer their ticket number and that the team will be in touch."
                value={f.success_message}
                onChange={(e) => set("success_message", e.target.value)}
                maxLength={500}
                disabled={ro}
              />
            </div>
          </Card>
          <Card title="When the assistant offers it">
            <div className="vr-form">
              {f.is_contact ? (
                <p className="vr-muted" style={{ margin: 0 }}>
                  The contact form is offered when a customer asks for a person, nobody replies in time, and none of your other forms fits the conversation.
                </p>
              ) : (
                <>
                  <label className="vr-field">
                    <span className="vr-field-top">Activation condition</span>
                    <span className="vr-input vr-textarea">
                      <textarea
                        rows={3}
                        value={f.activation_condition}
                        disabled={ro}
                        maxLength={2000}
                        placeholder="The customer wants to return or exchange an order."
                        aria-label="Activation condition"
                        onChange={(e) => set("activation_condition", e.target.value)}
                      />
                    </span>
                    <span className="vr-help">In plain words. The assistant checks it against the conversation and offers the form when it clearly applies. Leave empty to offer it only when the customer picks it.</span>
                  </label>
                  <div>
                    <span className="vr-field-top" style={{ display: "block", marginBottom: 6 }}>
                      Offered by
                    </span>
                    {(bots.data ?? []).length ? (
                      (bots.data ?? []).map((b) => (
                        <label key={b.id} className="vr-checkbox-row">
                          <input
                            type="checkbox"
                            disabled={ro}
                            checked={f.chatbot_ids.includes(b.id)}
                            onChange={(e) => set("chatbot_ids", e.target.checked ? [...f.chatbot_ids, b.id] : f.chatbot_ids.filter((x) => x !== b.id))}
                          />
                          <span>{b.name}</span>
                        </label>
                      ))
                    ) : (
                      <p className="vr-help">No chatbots yet.</p>
                    )}
                  </div>
                </>
              )}
            </div>
          </Card>
          <Card
            title={`Fields · ${f.fields.length}`}
            actions={
              manage ? (
                <Select ariaLabel="Add a field" value="" onChange={(v) => v && addField(v as FieldType)} options={[{ value: "", label: "Add a field…" }, ...TYPES]} />
              ) : null
            }
            padded={false}
          >
            {!f.fields.length ? (
              <p className="vr-muted" style={{ padding: "var(--space-4) var(--space-6)", margin: 0 }}>
                Add the details you need, such as an order number or an email.
              </p>
            ) : (
              <div className="vr-fieldlist" role="listbox" aria-label="Fields">
                {f.fields.map((x, i) => (
                  <div key={x.key + i} style={{ display: "flex", alignItems: "center" }}>
                    <button type="button" role="option" aria-selected={i === sel} className={`vr-fieldrow${i === sel ? " is-on" : ""}`} onClick={() => setSel(i)}>
                      <span>
                        <span>{x.label}</span>
                        <small>
                          {TYPE_LABEL[x.type]} · <span className="vr-mono">{x.key}</span>
                        </small>
                      </span>
                      {x.required ? <Badge>Required</Badge> : <Badge>Optional</Badge>}
                    </button>
                    {manage ? (
                      <span className="vr-actions" style={{ padding: "0 8px", flexWrap: "nowrap" }}>
                        <Button size="sm" variant="ghost" aria-label={`Move ${x.label} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                          ↑
                        </Button>
                        <Button size="sm" variant="ghost" aria-label={`Move ${x.label} down`} disabled={i === f.fields.length - 1} onClick={() => move(i, 1)}>
                          ↓
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          icon="x"
                          aria-label={`Remove ${x.label}`}
                          onClick={() => {
                            set("fields", f.fields.filter((_, j) => j !== i));
                            setSel(Math.max(0, i - 1));
                          }}
                        />
                      </span>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </Card>
          {field ? (
            <Card title={`Field · ${field.label}`}>
              <div className="vr-form">
                <div className="vr-form-row">
                  <Field label="Label" value={field.label} disabled={ro} maxLength={120} onChange={(e) => setField({ label: e.target.value })} />
                  <Select label="Type" value={field.type} disabled={ro} onChange={(v) => setField({ type: v as FieldType, validation: {}, options: v === "select" || v === "multiselect" ? (field.options.length ? field.options : ["Option 1"]) : [] })} options={TYPES} />
                </div>
                <div className="vr-form-row">
                  <Field label="Key" className="vr-mono" help="Used in exports and the ticket. Lowercase letters, numbers and _." value={field.key} disabled={ro} maxLength={40} onChange={(e) => setField({ key: e.target.value })} />
                  <Field label="Placeholder" value={field.placeholder ?? ""} disabled={ro || field.type === "checkbox" || field.type === "multiselect"} maxLength={120} onChange={(e) => setField({ placeholder: e.target.value })} />
                </div>
                <Field label="Help text" value={field.help ?? ""} disabled={ro} maxLength={300} onChange={(e) => setField({ help: e.target.value })} />
                <Switch label="Required" help={field.type === "checkbox" ? "The customer must tick it (for example, to accept a policy). The assistant never ticks it for them." : "The form can't be sent without it."} checked={field.required} disabled={ro} onChange={(v) => setField({ required: v })} />
                {field.type === "select" || field.type === "multiselect" ? (
                  <label className="vr-field">
                    <span className="vr-field-top">Options, one per line</span>
                    <span className="vr-input vr-textarea">
                      <textarea rows={4} disabled={ro} value={field.options.join("\n")} aria-label="Options" onChange={(e) => setField({ options: e.target.value.split("\n") })} />
                    </span>
                  </label>
                ) : null}
                <Rules field={field} setRule={setRule} ro={ro} />
              </div>
            </Card>
          ) : null}
        </div>
        <div className="vr-preview-frame">
          <span className="vr-eyebrow" style={{ display: "block", marginBottom: 8 }}>
            Preview · as customers see it
          </span>
          <div className="vr-wpreview">
            <div className="vr-wpreview-head">
              <span className="vr-wpreview-dot" />
              <b style={{ fontSize: 14 }}>{f.name || "Untitled form"}</b>
            </div>
            <div className="vr-wpreview-body">
              <div className="vr-wform-card">
                <b>{f.name || "Untitled form"}</b>
                {f.description ? <span className="vr-wform-sub">{f.description}</span> : null}
                <ChatFormFields
                  block={{ form_id: "preview", version: f.version ?? 1, title: f.name, description: f.description, fields: f.fields.map((x) => ({ ...x, options: x.options.filter(Boolean) })), submit_label: f.submit_label }}
                  values={Object.fromEntries(f.fields.map((x) => [x.key, preview[x.key] ?? emptyValue(x)]))}
                  onChange={(k, v) => setPreview({ ...preview, [k]: v })}
                  onSubmit={() => undefined}
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function Rules({ field, setRule, ro }: { field: FormField; setRule: (k: keyof FormField["validation"], v: string | number | boolean | undefined) => void; ro: boolean }) {
  const v = field.validation;
  const num = (s: string) => (s === "" ? undefined : Number(s));
  if (field.type === "text" || field.type === "textarea")
    return (
      <>
        <div className="vr-form-row">
          <Field label="Minimum length" type="number" min={0} className="vr-mono" disabled={ro} value={v.min_length ?? ""} onChange={(e) => setRule("min_length", num(e.target.value))} />
          <Field label="Maximum length" type="number" min={1} className="vr-mono" disabled={ro} value={v.max_length ?? ""} placeholder={field.type === "text" ? "500" : "4000"} onChange={(e) => setRule("max_length", num(e.target.value))} />
        </div>
        {field.type === "text" ? (
          <Field label="Pattern" className="vr-mono" help="Optional regular expression the whole answer must match, for example [0-9]{5,8} for an order number." disabled={ro} value={v.pattern ?? ""} maxLength={200} onChange={(e) => setRule("pattern", e.target.value)} />
        ) : null}
      </>
    );
  if (field.type === "number")
    return (
      <>
        <div className="vr-form-row">
          <Field label="Minimum" type="number" className="vr-mono" disabled={ro} value={(v.min as number | undefined) ?? ""} onChange={(e) => setRule("min", num(e.target.value))} />
          <Field label="Maximum" type="number" className="vr-mono" disabled={ro} value={(v.max as number | undefined) ?? ""} onChange={(e) => setRule("max", num(e.target.value))} />
        </div>
        <Switch label="Whole numbers only" checked={Boolean(v.integer)} disabled={ro} onChange={(on) => setRule("integer", on)} />
      </>
    );
  if (field.type === "multiselect")
    return (
      <div className="vr-form-row">
        <Field label="Choose at least" type="number" min={0} className="vr-mono" disabled={ro} value={v.min_selected ?? ""} onChange={(e) => setRule("min_selected", num(e.target.value))} />
        <Field label="Choose at most" type="number" min={1} className="vr-mono" disabled={ro} value={v.max_selected ?? ""} onChange={(e) => setRule("max_selected", num(e.target.value))} />
      </div>
    );
  if (field.type === "date")
    return (
      <div className="vr-form-row">
        <Field label="Earliest" type="date" disabled={ro} value={(v.min as string | undefined) ?? ""} onChange={(e) => setRule("min", e.target.value)} />
        <Field label="Latest" type="date" disabled={ro} value={(v.max as string | undefined) ?? ""} onChange={(e) => setRule("max", e.target.value)} />
      </div>
    );
  return null;
}
