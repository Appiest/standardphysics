import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AuthForm } from "@/components/auth/AuthForm";
import { SheetFrame } from "@/components/blueprint/SheetFrame";
import { SHEET_GRID_CLASS, SheetField } from "@/components/blueprint/SheetField";
import { currentSession } from "@/lib/session";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Sign in to Standard Physics",
  description: "See what gets in the way of customers in your shop, and how to fix it.",
};

export default async function SignInPage({ searchParams }: { searchParams: Promise<{ new?: string }> }) {
  if (await currentSession()) redirect("/");
  const wantsAnAccount = (await searchParams).new !== undefined;

  return (
    <main className="flex min-h-dvh px-3 py-3 sm:px-5 sm:py-5">
      <SheetFrame columns={8} rows={6} className="flex-1">
        <div className="grid gap-10 px-4 py-8 sm:px-8 sm:py-10 lg:min-h-full lg:grid-cols-[minmax(0,1fr)_24rem] lg:items-center lg:gap-16">
          <div className="flex min-w-0 flex-col gap-6">
            <h1 className="heading-display text-5xl text-balance sm:text-7xl">Standard Physics</h1>
            <p className="max-w-lg text-lg text-ink-muted text-pretty">
              Walk your shop with an iPhone. We measure every aisle, doorway and counter against the accessibility rules
              that apply to it, and show you what to move when something is too tight.
            </p>
            <dl className={`${SHEET_GRID_CLASS} max-w-lg grid-cols-1 border border-ink *:bg-sheet sm:grid-cols-2`}>
              <SheetField label="Measured against">ADA 2010 Standards</SheetField>
              <SheetField label="You need">An iPhone with LiDAR</SheetField>
            </dl>
          </div>
          <AuthForm initialMode={wantsAnAccount ? "sign-up" : "sign-in"} />
        </div>
      </SheetFrame>
    </main>
  );
}
