"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Field } from "@/components/ui/Field";

type Mode = "sign-in" | "sign-up";

const MIN_PASSWORD_LENGTH = 10;

const COPY: Record<Mode, { choose: string; submit: string; working: string }> = {
  "sign-in": { choose: "I have an account", submit: "Sign in", working: "Signing you in" },
  "sign-up": { choose: "I'm new here", submit: "Create the account", working: "Creating your account" },
};

async function submitCredentials(mode: Mode, form: FormData): Promise<string | null> {
  const body =
    mode === "sign-up"
      ? {
          email: form.get("email"),
          password: form.get("password"),
          shop_name: form.get("shop_name"),
        }
      : { email: form.get("email"), password: form.get("password") };
  const response = await fetch(`/api/auth/${mode}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.ok) return null;
  const problem = await response.json().catch(() => null);
  return problem?.error ?? "Something went wrong on our end. Try again.";
}

export function AuthForm({ initialMode }: { initialMode: Mode }) {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>(initialMode);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    const failure = await submitCredentials(mode, new FormData(event.currentTarget));
    if (failure) {
      setError(failure);
      setWorking(false);
      return;
    }
    router.replace("/");
    router.refresh();
  }

  function switchTo(next: Mode) {
    setMode(next);
    setError(null);
  }

  return (
    <div className="border border-ink bg-sheet">
      <div role="group" aria-label="Do you already have an account?" className="grid grid-cols-2 gap-px bg-ink">
        {(Object.keys(COPY) as Mode[]).map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={mode === option}
            onClick={() => switchTo(option)}
            className="bg-sheet px-4 py-3 text-sm font-medium text-ink-muted transition-colors aria-pressed:bg-ink aria-pressed:text-paper hover:text-ink aria-pressed:hover:text-paper"
          >
            {COPY[option].choose}
          </button>
        ))}
      </div>

      <form onSubmit={onSubmit} aria-label={COPY[mode].submit} className="flex flex-col gap-5 px-5 py-6">
        {mode === "sign-up" && (
          <Field
            id="shop_name"
            name="shop_name"
            label="What is your shop called?"
            required
            maxLength={120}
            autoComplete="organization"
            placeholder="Sunrise Boba"
          />
        )}
        <Field id="email" name="email" type="email" label="Email" required autoComplete="email" placeholder="you@yourshop.com" />
        <Field
          id="password"
          name="password"
          type="password"
          label="Password"
          required
          minLength={mode === "sign-up" ? MIN_PASSWORD_LENGTH : undefined}
          autoComplete={mode === "sign-up" ? "new-password" : "current-password"}
          hint={mode === "sign-up" ? `At least ${MIN_PASSWORD_LENGTH} characters.` : undefined}
        />

        <p role="alert" aria-live="polite" className={`text-sm text-problem ${error ? "" : "sr-only"}`}>
          {error}
        </p>

        <button
          type="submit"
          disabled={working}
          className="bg-ink px-5 py-3 text-base font-medium text-paper transition-colors hover:bg-ink/85 disabled:bg-ink/40"
        >
          {working ? COPY[mode].working : COPY[mode].submit}
        </button>
      </form>
    </div>
  );
}
