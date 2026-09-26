import { ArrowLeft } from "@phosphor-icons/react/dist/ssr";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { duration, funnelBars } from "@/components/team/funnelBars";
import { loadTeam } from "@/components/team/loadTeam";
import { buttonClassName } from "@/components/ui/Button";
import { isTeam, requireSession } from "@/lib/session";
import type { Funnel } from "@/types/contracts";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "How far owners get" };

/** Every shop on the server, step by step from the first walk to the first fix. Team only. */
export default async function TeamFunnelPage() {
  const session = await requireSession();
  if (!isTeam(session)) notFound();
  const funnel = await loadTeam<Funnel>("/api/team/funnel");
  const bars = funnelBars(funnel.steps);

  return (
    <main className="mx-auto max-w-3xl px-5 py-10">
      <div className="mb-10">
        <Link href="/" className={`-ms-3 ${buttonClassName("quiet")}`}>
          <ArrowLeft size={18} weight="bold" aria-hidden />
          Back to your shops
        </Link>
      </div>
      <h1 className="heading-display text-4xl">How far owners get</h1>
      <ol className="mt-8 flex flex-col gap-4">
        {bars.map((bar) => (
          <li key={bar.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1.5">
            <span className="font-medium">{bar.label}</span>
            <span className="measurement text-lg">{bar.shops} {bar.shops === 1 ? "shop" : "shops"}</span>
            <span className="col-span-2 h-2.5 bg-ink/[0.06]" aria-hidden>
              <span className="block h-full bg-accent" style={{ width: `${bar.share * 100}%` }} />
            </span>
            {bar.lost > 0 && <span className="col-span-2 text-sm text-ink-muted">{bar.lost} stopped before this step</span>}
          </li>
        ))}
      </ol>
      <dl className="mt-12 grid gap-x-8 gap-y-2 border-t border-ink pt-6 sm:grid-cols-[auto_1fr]">
        <dt className="text-ink-muted">From the walk to results, median</dt>
        <dd className="measurement">{duration(funnel.median_minutes_to_results, "minutes")}</dd>
        <dt className="text-ink-muted">From results to the first fix, median</dt>
        <dd className="measurement">{duration(funnel.median_hours_to_first_fix, "hours")}</dd>
      </dl>
    </main>
  );
}
