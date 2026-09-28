import { access } from "node:fs/promises";
import path from "node:path";
import { ArrowLeft, ArrowSquareOut } from "@phosphor-icons/react/dist/ssr";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { NOTEBOOK_ROOT } from "@/components/team/notebookFiles";
import { NOTEBOOK_URL } from "@/components/team/notebookUrl";
import { buttonClassName } from "@/components/ui/Button";
import { isTeam, requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Fine-tuning" };

async function notebookExported() {
  try {
    await access(path.join(NOTEBOOK_ROOT, "index.html"));
    return true;
  } catch {
    return false;
  }
}

function NotExported() {
  return (
    <div className="mt-8 border border-ink bg-sheet p-6">
      <p className="font-medium">This server has no copy of the notebook yet.</p>
      <p className="mt-2 text-ink-muted">
        Build it from <code className="measurement">apps/web</code> with <code className="measurement">npm run notebook:export</code>, then reload.
      </p>
    </div>
  );
}

/** notebooks/finetune_story.py, running as marimo in the browser, so its picker and table work here. Team only. */
export default async function TeamTrainingPage() {
  const session = await requireSession();
  if (!isTeam(session)) notFound();
  const exported = await notebookExported();

  return (
    <main className="mx-auto max-w-7xl px-5 py-10">
      <div className="mb-10">
        <Link href="/" className={`-ms-3 ${buttonClassName("quiet")}`}>
          <ArrowLeft size={18} weight="bold" aria-hidden />
          Back to your shops
        </Link>
      </div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="heading-display text-4xl">Fine-tuning</h1>
          <p className="mt-2 max-w-prose text-ink-muted">
            This is our marimo notebook running in your browser. Pick a benchmark and it re-runs its own cells here.
          </p>
        </div>
        {exported && (
          <a href={NOTEBOOK_URL} target="_blank" rel="noopener" className={buttonClassName("quiet")}>
            <ArrowSquareOut size={18} aria-hidden />
            Open the notebook full screen
          </a>
        )}
      </div>
      {exported ? (
        <iframe src={NOTEBOOK_URL} title="Fine-tuning notebook, running in marimo" className="mt-8 h-[85dvh] w-full border border-ink bg-paper" />
      ) : (
        <NotExported />
      )}
    </main>
  );
}
