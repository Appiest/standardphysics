import type { Metadata } from "next";
import Link from "next/link";
import { SheetFrame } from "@/components/blueprint/SheetFrame";
import { WaitlistForm } from "@/components/waitlist/WaitlistForm";

export const metadata: Metadata = {
  title: "Try Standard Physics",
  description: "Join the Standard Physics TestFlight waitlist for students and shop owners.",
};

export default function WaitlistPage() {
  return (
    <main className="flex min-h-dvh px-3 py-3 sm:px-5 sm:py-5">
      <SheetFrame columns={8} rows={6} className="flex-1">
        <div className="grid gap-10 px-4 py-8 sm:px-8 sm:py-10 lg:min-h-full lg:grid-cols-[minmax(0,1fr)_24rem] lg:items-center lg:gap-16">
          <div className="flex min-w-0 flex-col gap-6">
            <h1 className="heading-display text-5xl text-balance sm:text-7xl">Try Standard Physics</h1>
            <p className="max-w-lg text-lg text-ink-muted text-pretty">
              We’re inviting students and shop owners to try our iPhone app. Join the waitlist and we’ll email you a
              TestFlight invite when a spot opens.
            </p>
            <p className="max-w-lg text-ink-muted">Scanning a shop needs an iPhone with LiDAR, such as an iPhone 12 Pro or newer Pro model.</p>
            <div className="flex flex-wrap gap-x-6 gap-y-3 text-sm">
              <Link href="/sign-in" className="underline decoration-rule underline-offset-2">Sign in to your workspace</Link>
              <Link href="/privacy" className="underline decoration-rule underline-offset-2">Read how we use your email</Link>
            </div>
          </div>
          <WaitlistForm />
        </div>
      </SheetFrame>
    </main>
  );
}
