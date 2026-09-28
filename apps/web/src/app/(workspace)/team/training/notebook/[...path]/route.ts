import { readFile } from "node:fs/promises";
import { cacheControl, contentType, NOTEBOOK_ROOT, notebookFile } from "@/components/team/notebookFiles";
import { currentSession, isTeam } from "@/lib/session";

export const dynamic = "force-dynamic";

const notFound = () => new Response("Not found", { status: 404 });

/** The exported fine-tuning notebook, file by file, to team accounts only: it carries spend and model names. */
export async function GET(_request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const session = await currentSession();
  if (!session || !isTeam(session)) return notFound();

  const segments = (await params).path;
  const file = notebookFile(NOTEBOOK_ROOT, segments);
  if (!file) return notFound();

  try {
    const body = await readFile(file);
    return new Response(body, {
      headers: { "Content-Type": contentType(file), "Cache-Control": cacheControl(segments) },
    });
  } catch {
    return notFound();
  }
}
