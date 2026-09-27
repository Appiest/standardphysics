import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { Session } from "@/types/contracts";
import { API_ORIGIN } from "./api-origin";

export const SESSION_COOKIE = "sp_session";

export type { Session };

/** Team accounts see the builders' tools; owners never do. */
export function isTeam(session: Session): boolean {
  return session.role === "team";
}

/** The signed-in owner, or null when the cookie is missing, expired or revoked. */
export async function currentSession(): Promise<Session | null> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return null;
  const response = await fetch(`${API_ORIGIN}/api/auth/session`, {
    cache: "no-store",
    headers: { cookie: `${SESSION_COOKIE}=${token}` },
  });
  return response.ok ? ((await response.json()) as Session) : null;
}

/** The signed-in owner, or a redirect to the sign-in screen. */
export async function requireSession(): Promise<Session> {
  const session = await currentSession();
  if (!session) redirect("/sign-in");
  return session;
}
