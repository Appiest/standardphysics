"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import type { ReplayChapter, SimulationReplay } from "@/types/contracts";

const OUTCOMES: Record<ReplayChapter["outcome"], string> = {
  route_blocked: "Route blocked",
  out_of_reach: "Outside estimated reach",
  route_and_reach_fit: "Route and reach fit the model",
};

export function SimulationReplayPlayer({ replay }: { replay: SimulationReplay }) {
  const video = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(0);
  const [error, setError] = useState(false);
  const base = `/api/scans/${replay.scan_id}/revisions/${replay.revision}/replay`;
  const active = replay.chapters.findLastIndex((chapter) => chapter.seconds <= time);

  function seek(seconds: number) {
    if (!video.current) return;
    video.current.currentTime = seconds;
    setTime(seconds);
  }

  return <div className="space-y-6">
    <video ref={video} controls playsInline preload="metadata" className="aspect-video w-full rounded-xl bg-ink"
      aria-label="Recorded scan simulations with eye-level and third-person views"
      aria-describedby="replay-description" src={`${base}/video.mp4`}
      onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)} onError={() => setError(true)} />
    {error && <p role="alert" className="text-problem">The video could not load. Reload this page or download the recording below.</p>}
    <p id="replay-description" className="max-w-3xl text-sm text-ink-muted">Both views replay the same wheelchair journey through the scanned room. The third-person view cuts away upper surfaces for visibility. Props are hypothetical and hand motion is illustrative. Select a task to seek, then press play.</p>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="heading-display text-xl">Recorded tasks</h2>
      <a className="py-2 text-sm text-accent underline underline-offset-4" href={`${base}/video.mp4`} download="simulation-replay.mp4">Download video</a>
    </div>
    <ol className="grid gap-2 sm:grid-cols-2">
      {replay.chapters.map((chapter, index) => <li key={chapter.evaluation}>
        <Button className="w-full justify-between text-start aria-[current=step]:bg-rule/50" aria-current={active === index ? "step" : undefined}
          variant="quiet" onClick={() => seek(chapter.seconds)}
          disabled={error}>
          <span className="min-w-0 space-y-1">
            <span className="block font-medium">{chapter.task}</span>
            <span className="block text-sm text-ink-muted">{OUTCOMES[chapter.outcome]}</span>
          </span>
          <span className="shrink-0 text-sm">{active === index ? "Selected" : `${chapter.seconds}s`}</span>
        </Button>
      </li>)}
    </ol>
  </div>;
}
