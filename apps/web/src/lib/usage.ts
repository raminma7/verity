import { msg } from "@/i18n";
/** Names of metered AI operations, as customers see them. */
export const OPS: Record<string, string> = {
  chat_response: msg("Chatbot answers"),
  kb_ask: msg("Ask (dashboard)"),
  tool_call: msg("API tool decisions"),
  form_activation: msg("Form suggestions"),
  form_prefill: msg("Form prefill"),
  classification: msg("Email classification"),
  summary: msg("Summaries"),
  auto_reply: msg("Automatic replies"),
  embedding: msg("Embeddings"),
};
