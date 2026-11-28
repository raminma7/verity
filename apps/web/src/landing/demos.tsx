"use client";

/* The few interactive pieces of the landing page. Each one shows the real product component with fixed sample
   content, already translated on the server and passed in; the product components that translate themselves
   (confidence, citations, trace) read their strings from an I18nScope instead of the dashboard's global state. */
import { useState } from "react";

import { ChatFormFields, type ChatFormCopy } from "@/components/chat-form";
import { AnswerCard, CitedText, SourceCard, type AnswerBlock, type Confidence } from "@/components/rag";
import type { FormBlock, FormValue } from "@/lib/api";
import { I18nScope } from "@/i18n/scope";
import type { Dict, Locale } from "@/i18n";

export type DemoSource = { n: number; type: string; title: string; section: string; excerpt: string; highlight: string; score: number };

/** An answer whose citation chips select the passage they point to, as in the product. */
export function CiteDemo({
  locale,
  scope,
  question,
  label,
  blocks,
  sources,
  confidence,
}: {
  locale: Locale;
  scope: Dict;
  question: string;
  label: string;
  blocks: AnswerBlock[];
  sources: DemoSource[];
  confidence: Confidence;
}) {
  const [active, setActive] = useState<number | null>(sources[0]?.n ?? null);
  const titles = Object.fromEntries(sources.map((s) => [s.n, s.title]));
  return (
    <I18nScope locale={locale} strings={scope}>
      <div className="vr-land-demo">
        <AnswerCard confidence={confidence} question={question} label={label} sourceCount={sources.length}>
          <CitedText blocks={blocks} active={active} onCite={setActive} titles={titles} />
        </AnswerCard>
        <div className="vr-land-sources">
          {sources.map((s) => (
            <SourceCard key={s.n} n={s.n} type={s.type} title={s.title} section={s.section} score={s.score} excerpt={s.excerpt} highlight={s.highlight} active={active === s.n} onClick={() => setActive(s.n)} />
          ))}
        </div>
      </div>
    </I18nScope>
  );
}

/** A chatbot form as the customer sees it: prefilled from the chat, and nothing is sent until they confirm. */
export function FormDemo({ locale, scope, block, initial, copy, caption }: { locale: Locale; scope: Dict; block: FormBlock; initial: Record<string, FormValue>; copy: ChatFormCopy; caption: string }) {
  const [values, setValues] = useState(initial);
  const [sent, setSent] = useState(false);
  return (
    <I18nScope locale={locale} strings={scope}>
      <div className="vr-wform-card vr-land-form">
        <b>{block.title}</b>
        <span className="vr-wform-sub">{block.description}</span>
        <ChatFormFields block={block} values={values} onChange={(k, v) => setValues((c) => ({ ...c, [k]: v }))} onSubmit={() => setSent(true)} copy={copy} prefilled readOnly={sent} />
        {sent ? (
          <p className="vr-wform-note" role="status">
            {caption}
          </p>
        ) : null}
      </div>
    </I18nScope>
  );
}
