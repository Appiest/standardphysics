"use client";

import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";

type Role = "student" | "shop_owner";

export function WaitlistForm() {
  const [working, setWorking] = useState(false);
  const [joined, setJoined] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: form.get("email"), role: form.get("role") as Role }),
      });
      if (!response.ok) throw new Error("We couldn’t save your place. Please try again in a few minutes.");
      setJoined(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "We couldn’t save your place. Please try again.");
    } finally {
      setWorking(false);
    }
  }

  if (joined) {
    return (
      <div className="border border-ink bg-sheet px-6 py-8" role="status">
        <h2 className="heading-display text-2xl">You’re on the list</h2>
        <p className="mt-3 text-ink-muted">We’ll send a TestFlight invite to your email when a spot opens.</p>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5 border border-ink bg-sheet px-5 py-6 sm:px-6">
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-sm font-medium">I’m a</legend>
        <label className="flex min-h-11 items-center gap-3 text-base">
          <input type="radio" name="role" value="shop_owner" defaultChecked className="size-4 accent-accent" />
          Shop owner
        </label>
        <label className="flex min-h-11 items-center gap-3 text-base">
          <input type="radio" name="role" value="student" className="size-4 accent-accent" />
          Student
        </label>
      </fieldset>
      <Field
        id="waitlist-email"
        name="email"
        type="email"
        label="Email for your TestFlight invite"
        required
        maxLength={254}
        autoComplete="email"
        placeholder="you@example.com"
      />
      <p role="alert" className={`text-sm text-problem ${error ? "" : "sr-only"}`}>{error}</p>
      <Button
        type="submit"
        variant="primary"
        squared
        disabled={working}
        className="min-h-11 justify-center disabled:bg-ink/40"
      >
        {working ? "Joining the waitlist" : "Join the TestFlight waitlist"}
      </Button>
    </form>
  );
}
