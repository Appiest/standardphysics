"use client";

import { Copy, FilePdf, FileText, ShareNetwork } from "@phosphor-icons/react";
import Link from "next/link";
import { useState } from "react";
import { Button, buttonClassName } from "@/components/ui/Button";
import { tellApp } from "@/lib/native-bridge";
import { shareReport, stopSharing } from "@/lib/owner-client";

/** A link a contractor, landlord or inspector can open, or the same report as a PDF. */
export function SharePanel({ scanId, shopName, onShared }: { scanId: string; shopName: string; onShared: () => void }) {
  const [link, setLink] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const share = async () => {
    setWorking(true);
    setNote(null);
    try {
      const made = await shareReport(scanId);
      const url = `${window.location.origin}${made.path}`;
      setLink(url);
      tellApp({ type: "share", url, title: `${shopName} report` });
      onShared();
    } catch {
      setNote("We couldn't make a link. Try again.");
    } finally {
      setWorking(false);
    }
  };

  const [confirmingStop, setConfirmingStop] = useState(false);

  const stop = async () => {
    setConfirmingStop(false);
    setNote(null);
    try {
      await stopSharing(scanId);
      setLink(null);
      setNote("Every link to this report has stopped working.");
    } catch {
      setNote("We couldn't stop the links. Try again.");
    }
  };

  const copy = async () => {
    if (!link) return;
    await navigator.clipboard.writeText(link).then(() => setNote("Link copied."), () => setNote("Select the link and copy it."));
  };

  return (
    <section className="flex flex-col gap-3 rounded-2xl bg-sheet p-4 shadow-float" aria-labelledby="share-heading">
      <h2 id="share-heading" className="text-lg font-semibold">Your report</h2>
      <Link href={`/scans/${scanId}/report`} className={`${buttonClassName("primary")} justify-center`}>
        <FileText size={20} weight="bold" aria-hidden />
        See and print the report
      </Link>
      {link ? (
        <div className="flex flex-col gap-2">
          <p className="measurement break-all rounded-lg bg-ink/[0.05] px-3 py-2 text-sm">{link}</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="choice" onClick={copy}><Copy size={18} weight="bold" aria-hidden />Copy the link</Button>
            <a href={link} target="_blank" rel="noreferrer" className={buttonClassName("choice")}><FilePdf size={18} weight="bold" aria-hidden />Open it to save a PDF</a>
          </div>
        </div>
      ) : (
        <Button variant="choice" className="justify-center" disabled={working} onClick={share}>
          <ShareNetwork size={20} weight="bold" aria-hidden />
          {working ? "Making a link" : "Share a link for 30 days"}
        </Button>
      )}
      <p role="status" className="text-sm text-ink-muted">{note}</p>
      {confirmingStop ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="w-full text-sm">Anyone you sent a link to won&rsquo;t be able to open it. You can share again after.</p>
          <Button variant="danger" className="-ms-3" onClick={stop}>Stop every link</Button>
          <Button onClick={() => setConfirmingStop(false)}>Keep them</Button>
        </div>
      ) : (
        <Button variant="danger" className="-ms-3 self-start" onClick={() => setConfirmingStop(true)}>Stop every link to this report</Button>
      )}
    </section>
  );
}
