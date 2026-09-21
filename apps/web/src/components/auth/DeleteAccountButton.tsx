"use client";

import { Trash } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/Button";

/**
 * Asks in place rather than in a dialog, so the consequence is on screen
 * beside the button that carries it out.
 */
export function DeleteAccountButton() {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [failure, setFailure] = useState("");

  async function deleteAccount() {
    setDeleting(true);
    setFailure("");
    const response = await fetch("/api/account", { method: "DELETE" });
    if (!response.ok) {
      setDeleting(false);
      setFailure("Unable to delete your account. Check your connection and try again.");
      return;
    }
    router.replace("/sign-in");
    router.refresh();
  }

  if (!confirming) {
    return (
      <Button squared variant="danger" onClick={() => setConfirming(true)}>
        <Trash size={18} aria-hidden />
        Delete account
      </Button>
    );
  }

  return (
    <div className="flex flex-col items-start gap-3">
      <p className="text-sm text-ink-muted">
        Every shop you have scanned, and every measurement taken in one, goes with it. There is no undo.
      </p>
      <div className="flex items-center gap-2">
        <Button squared variant="danger" onClick={deleteAccount} disabled={deleting}>
          <Trash size={18} aria-hidden />
          {deleting ? "Deleting account" : "Delete account"}
        </Button>
        <Button squared onClick={() => setConfirming(false)} disabled={deleting}>
          Keep it
        </Button>
      </div>
      {failure ? (
        <p className="text-sm text-problem" role="alert">
          {failure}
        </p>
      ) : null}
    </div>
  );
}
