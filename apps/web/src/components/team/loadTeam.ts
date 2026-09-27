import { cookies } from "next/headers";
import { notFound } from "next/navigation";
import { fetchApi } from "@/lib/api-fetch";
import { API_ORIGIN } from "@/lib/api-origin";
import { SESSION_COOKIE } from "@/lib/session";

/** A team-only API read, with the browser's session forwarded by hand the way `lib/api.ts` does. */
export async function loadTeam<T>(path: string): Promise<T> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  const response = await fetchApi(`${API_ORIGIN}${path}`, {
    cache: "no-store",
    headers: token ? { cookie: `${SESSION_COOKIE}=${token}` } : {},
  });
  if (response.status === 403) notFound();
  if (!response.ok) throw new Error(`${path} answered ${response.status}`);
  return (await response.json()) as T;
}
