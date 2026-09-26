import { ArrowLeft } from "@phosphor-icons/react/dist/ssr";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { loadReviewQueue } from "@/components/team/loadReviewQueue";
import { ReviewQueueList } from "@/components/team/ReviewQueueList";
import { buttonClassName } from "@/components/ui/Button";
import { isTeam, requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Photos to check" };

export default async function TeamReviewsPage() {
  const session = await requireSession();
  if (!isTeam(session)) notFound();
  const { reviews } = await loadReviewQueue();

  return (
    <main className="mx-auto max-w-5xl px-5 py-10">
      <div className="mb-10">
        <Link href="/" className={`-ms-3 ${buttonClassName("quiet")}`}>
          <ArrowLeft size={18} weight="bold" aria-hidden />
          Back to your shops
        </Link>
      </div>
      <ReviewQueueList reviews={reviews} />
    </main>
  );
}
