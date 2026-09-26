"use client";

import { Camera, CheckCircle } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import { type ChangeEvent, type FormEvent, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { tellApp } from "@/lib/native-bridge";
import { answerRequest, sendPhoto, skipRequest } from "@/lib/owner-client";
import type { OwnerRequest } from "@/types/contracts";

const UNIT_WORDS = { in: "inches", lb: "pounds" } as const;

function useRequestAction(scanId: string) {
  const router = useRouter();
  const [working, setWorking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const run = async (action: () => Promise<unknown>) => {
    setWorking(true);
    setProblem(null);
    try {
      await action();
      router.refresh();
    } catch {
      setProblem("That didn't go through. Try again.");
    } finally {
      setWorking(false);
    }
  };
  return { working, problem, run, scanId };
}

type Action = ReturnType<typeof useRequestAction>;

/** One request per card, each with the one way to answer it. */
export function RequestList({ scanId, requests }: { scanId: string; requests: OwnerRequest[] }) {
  return (
    <ul className="flex flex-col gap-4">
      {requests.map((request) => (
        <li key={request.id}>
          <RequestCard scanId={scanId} request={request} />
        </li>
      ))}
    </ul>
  );
}

function RequestCard({ scanId, request }: { scanId: string; request: OwnerRequest }) {
  const action = useRequestAction(scanId);
  return (
    <article className="flex flex-col gap-3 rounded-2xl bg-sheet p-4 shadow-float">
      <h2 className="text-lg font-semibold leading-snug">{request.title}</h2>
      {request.kind !== "yes_no" && <p className="text-pretty text-ink-muted">{request.detail}</p>}
      <Answer request={request} action={action} />
      {action.problem && <p role="alert" className="text-sm text-problem">{action.problem}</p>}
    </article>
  );
}

function Answer({ request, action }: { request: OwnerRequest; action: Action }) {
  if (request.status === "answered" || request.status === "checked") return <Sent request={request} />;
  if (request.kind === "yes_no") return <YesNo request={request} action={action} />;
  if (request.kind === "number") return <NumberAnswer request={request} action={action} />;
  if (request.kind === "photo") return <PhotoAnswer request={request} action={action} />;
  return null;
}

function Sent({ request }: { request: OwnerRequest }) {
  const waiting = request.status === "answered";
  return (
    <p className="flex items-center gap-2 font-medium text-pass">
      <CheckCircle size={20} weight="fill" aria-hidden />
      {waiting ? "Sent. A person on our team is checking it." : "Done"}
    </p>
  );
}

function YesNo({ request, action }: { request: OwnerRequest; action: Action }) {
  const say = (yes: boolean) => action.run(() => answerRequest(action.scanId, request.id, { yes }));
  return (
    <div className="grid grid-cols-2 gap-3">
      <Button variant="choice" className="justify-center" disabled={action.working} onClick={() => say(true)}>Yes</Button>
      <Button variant="choice" className="justify-center" disabled={action.working} onClick={() => say(false)}>No</Button>
    </div>
  );
}

function NumberAnswer({ request, action }: { request: OwnerRequest; action: Action }) {
  const id = useId();
  const [value, setValue] = useState("");
  const unit = request.unit ? UNIT_WORDS[request.unit] : "";
  const label = unit ? `How many ${unit}?` : "Your number";
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const number = Number(value);
    if (number > 0) void action.run(() => answerRequest(action.scanId, request.id, { number }));
  };
  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <Field id={id} label={label} type="number" inputMode="decimal" min="0" step="0.1" required value={value}
        onChange={(event) => setValue(event.target.value)} className="measurement text-lg" />
      <Button type="submit" variant="primary" className="justify-center" disabled={action.working}>Send</Button>
      <SkipButton request={request} action={action} />
    </form>
  );
}

function PhotoAnswer({ request, action }: { request: OwnerRequest; action: Action }) {
  const input = useRef<HTMLInputElement>(null);
  const take = () => {
    if (!tellApp({ type: "takePhoto", requestId: request.id })) input.current?.click();
  };
  const chosen = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) void action.run(() => sendPhoto(action.scanId, request.id, file));
  };
  return (
    <div className="flex flex-col gap-3">
      <Button variant="primary" className="justify-center" disabled={action.working} onClick={take}>
        <Camera size={20} weight="bold" aria-hidden />
        Take a photo
      </Button>
      <input ref={input} type="file" accept="image/jpeg,image/png" capture="environment" className="sr-only" tabIndex={-1} aria-hidden onChange={chosen} />
      <SkipButton request={request} action={action} />
    </div>
  );
}

function SkipButton({ request, action }: { request: OwnerRequest; action: Action }) {
  if (request.status === "skipped") return <p className="text-sm text-ink-muted">You skipped this. It stays on your report until you send it.</p>;
  return (
    <Button className="-ms-3 self-start" disabled={action.working} onClick={() => action.run(() => skipRequest(action.scanId, request.id))}>
      Skip for now
    </Button>
  );
}
