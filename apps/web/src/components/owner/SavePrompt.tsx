"use client";

import { AppleLogo } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { ApiRefusal } from "@/lib/layout-client";
import { tellApp } from "@/lib/native-bridge";
import { saveAccount } from "@/lib/owner-client";

/**
 * Keep a guest's shop, asked at the first check-off or share. It never blocks
 * what the owner was doing: the status is already saved when this opens.
 */
export function SavePrompt({ open, inApp, onClose }: { open: boolean; inApp: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (open && element && !element.open) element.showModal();
    if (!open && element?.open) element.close();
  }, [open]);

  return (
    <dialog ref={dialog} onClose={onClose} aria-labelledby="save-heading"
      className="m-auto w-[min(100%-2rem,28rem)] rounded-2xl bg-sheet p-5 text-ink shadow-float backdrop:bg-ink/40 max-sm:mx-0 max-sm:mb-0 max-sm:mt-auto max-sm:w-full max-sm:max-w-none max-sm:rounded-b-none max-sm:pb-[max(1.25rem,env(safe-area-inset-bottom))]">
      <h2 id="save-heading" className="heading-display text-2xl">Save your shop</h2>
      <p className="mt-2 text-pretty text-ink-muted">Add your email so your shop and your checklist are here next time, on any device.</p>
      {inApp && (
        <Button variant="primary" className="mt-5 w-full justify-center" onClick={() => { tellApp({ type: "saveReport" }); onClose(); }}>
          <AppleLogo size={20} weight="fill" aria-hidden />
          Continue with Apple
        </Button>
      )}
      <EmailForm onSaved={onClose} primary={!inApp} />
      <Button className="mt-2 w-full justify-center" onClick={onClose}>Not now</Button>
    </dialog>
  );
}

function EmailForm({ onSaved, primary }: { onSaved: () => void; primary: boolean }) {
  const router = useRouter();
  const id = useId();
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setSaving(true);
    setProblem(null);
    try {
      await saveAccount(String(form.get("email")), String(form.get("password")));
      router.refresh();
      onSaved();
    } catch (error) {
      setProblem(error instanceof ApiRefusal && error.error ? error.error : "That didn't save. Try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-5 flex flex-col gap-3">
      <Field id={`${id}-email`} name="email" type="email" label="Email" autoComplete="email" required />
      <Field id={`${id}-password`} name="password" type="password" label="Password" autoComplete="new-password" minLength={10} required hint="At least 10 characters." />
      {problem && <p role="alert" className="text-sm text-problem">{problem}</p>}
      <Button type="submit" variant={primary ? "primary" : "chip"} className="justify-center" disabled={saving}>
        {saving ? "Saving" : "Save with my email"}
      </Button>
    </form>
  );
}
