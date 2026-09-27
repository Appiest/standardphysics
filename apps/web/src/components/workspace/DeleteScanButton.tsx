"use client";

import { Trash } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, buttonClassName } from "@/components/ui/Button";
import { MENU_ITEM } from "@/components/ui/Menu";
import { tellApp } from "@/lib/native-bridge";

/**
 * Removing one shop, from the page that shows it.
 *
 * The phone could already do this and the workspace could not, so a scan taken
 * on a phone that is no longer to hand could only be removed by deleting the
 * whole account.
 *
 * Asks in place rather than in a dialog, so the consequence is on screen beside
 * the button that carries it out. A room still being measured goes from the
 * owner's list at once, and the server finishes deleting it when the measuring
 * stops. Inside the iPhone app the page tells the app, which goes back home.
 */
const TRIGGER = {
  menu: `${MENU_ITEM} hover:bg-problem/10 hover:text-problem`,
  page: `${buttonClassName("quiet")} self-start text-ink-muted hover:text-problem`,
};

const CONFIRMATION = {
  menu: "flex max-w-72 flex-col items-start gap-2 p-3",
  page: "flex max-w-sm flex-col items-start gap-2",
};

export function DeleteScanButton({ scanId, name, look = "menu" }: { scanId: string; name: string; look?: keyof typeof TRIGGER }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [failure, setFailure] = useState("");

  async function deleteScan() {
    setDeleting(true);
    setFailure("");
    const response = await fetch(`/api/scans/${scanId}`, { method: "DELETE" });
    if (!response.ok) {
      setDeleting(false);
      setFailure(await refusal(response));
      return;
    }
    tellApp({ type: "shopDeleted", scanId });
    router.replace("/");
    router.refresh();
  }

  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className={TRIGGER[look]}
      >
        <Trash size={18} aria-hidden />
        Delete this shop
      </button>
    );
  }

  return (
    <div className={CONFIRMATION[look]}>
      <p className="text-sm text-ink-muted">
        Delete {name}? The room, the walkthrough and every finding go with it.
      </p>
      <Button squared variant="danger" onClick={deleteScan} disabled={deleting}>
        <Trash size={18} aria-hidden />
        {deleting ? "Deleting" : "Delete it"}
      </Button>
      <Button squared onClick={() => setConfirming(false)} disabled={deleting}>
        Keep it
      </Button>
      {failure ? (
        <p className="w-full text-sm text-problem" role="alert">
          {failure}
        </p>
      ) : null}
    </div>
  );
}

/** What the server said, when it said anything a person can act on. */
async function refusal(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string" && body.detail) return body.detail;
    if (typeof body?.error === "string" && body.error) return body.error;
  } catch {
    // A refusal with no readable body still has to say something.
  }
  return "Unable to delete this shop. Check your connection and try again.";
}
