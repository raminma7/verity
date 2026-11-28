import { msg, t } from "@/i18n";

/** The built-in roles' names (seeded in English by the API); custom role names are shown as written. */
msg("Workspace Owner");
msg("Workspace Administrator");
msg("Support Agent");
msg("Viewer");

export function roleName(name: string): string {
  return t(name);
}
