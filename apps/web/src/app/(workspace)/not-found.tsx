import Link from "next/link";
import { SheetFrame } from "@/components/blueprint/SheetFrame";

export default function NotFound() {
  return (
    <main className="flex min-h-dvh px-3 py-3 sm:px-5 sm:py-5">
      <SheetFrame columns={8} rows={6} className="flex-1">
        <div className="flex h-full flex-col items-start justify-center gap-5 px-4 py-10 sm:px-8">
          <h1 className="heading-display text-4xl text-balance sm:text-5xl">No such sheet</h1>
          <p className="max-w-md text-ink-muted text-pretty">
            This shop was deleted, or it belongs to another account.
          </p>
          <Link href="/" className="bg-ink px-5 py-3 text-base font-medium text-paper transition-colors hover:bg-ink/85">
            Back to your shops
          </Link>
        </div>
      </SheetFrame>
    </main>
  );
}
