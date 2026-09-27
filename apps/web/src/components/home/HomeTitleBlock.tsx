import { ChartBar, Images } from "@phosphor-icons/react/dist/ssr";
import Link from "next/link";
import { DeleteAccountButton } from "@/components/auth/DeleteAccountButton";
import { DeveloperModeToggle } from "@/components/auth/DeveloperModeToggle";
import { SignOutButton } from "@/components/auth/SignOutButton";
import { SheetField } from "@/components/blueprint/SheetField";
import { ScanShopButton } from "@/components/ScanShopButton";
import { buttonClassName } from "@/components/ui/Button";
import { isTeam, type Session } from "@/lib/session";

function TeamTools() {
  return (
    <div className="flex flex-col items-start gap-3 border-t border-ink p-4">
      <Link href="/team/reviews" className={`-ms-3 ${buttonClassName("quiet", true)}`}>
        <Images size={18} aria-hidden />
        Check owners&apos; photos
      </Link>
      <Link href="/team/funnel" className={`-ms-3 ${buttonClassName("quiet", true)}`}>
        <ChartBar size={18} aria-hidden />
        See how far owners get
      </Link>
      <DeveloperModeToggle />
    </div>
  );
}

export function HomeTitleBlock({ session, className = "" }: { session: Session; className?: string }) {
  return (
    <section aria-label="Title block" className={`border border-ink bg-paper ${className}`}>
      <p className="heading-display border-b border-ink px-4 py-3 text-2xl">Standard Physics</p>
      <SheetField label="Signed in as" className="border-b border-ink py-3">
        <span className="block truncate font-semibold">{session.shop_name}</span>
        <span className="block truncate font-normal text-ink-muted">{session.email}</span>
      </SheetField>
      <div className="flex flex-col items-start gap-3 p-4">
        <ScanShopButton />
        <SignOutButton />
        <DeleteAccountButton />
      </div>
      {isTeam(session) && <TeamTools />}
    </section>
  );
}
