"use client";

import { ArrowCounterClockwise, CircleNotch, Path } from "@phosphor-icons/react";
import { ActivitySpinner } from "@/components/ui/ActivitySpinner";
import { Button } from "@/components/ui/Button";
import type { RouteState } from "./useRoute";

export function RoutePanel({ route }: { route: RouteState }) {
  if (!route.scenario) return <p className="px-3 text-ink-muted">The shop is still being measured.</p>;
  const ready = route.changed || !route.confirmed;
  return (
    <div className="flex flex-col gap-5 px-3">
      <p className="text-ink-muted">
        Drag each marker to where customers really go. We check the path between each stop and the next.
      </p>
      <ol className="flex list-decimal flex-col gap-1 pl-5">
        {route.scenario.stops.map((stop, index) => (
          <li key={`${stop.name}-${index}`}>{stop.name}</li>
        ))}
      </ol>
      {route.problem && <ActivitySpinner />}
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" onClick={route.confirm} disabled={!ready || route.saving} className="disabled:opacity-40">
          {route.saving ? <CircleNotch size={18} className="animate-spin" aria-hidden /> : <Path size={18} weight="bold" aria-hidden />}
          {route.confirmed ? "Check the new route" : "Check these paths"}
        </Button>
        {route.changed && (
          <Button onClick={route.reset}>
            <ArrowCounterClockwise size={16} weight="bold" aria-hidden />
            Put the markers back
          </Button>
        )}
      </div>
    </div>
  );
}
