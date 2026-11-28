import { apiUrl } from "./site";

/** Whether the API has a contact address configured (GET /api/public/contact answers 404 when it doesn't).
 *  The form is shown only then. */
export async function fetchContactEnabled(): Promise<boolean> {
  try {
    const r = await fetch(`${apiUrl()}/api/public/contact`, { next: { revalidate: 300 }, signal: AbortSignal.timeout(3000) });
    return r.ok;
  } catch {
    return false;
  }
}
