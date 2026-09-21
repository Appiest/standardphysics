"use client";

import { ArrowClockwise, ArrowCounterClockwise, CircleNotch } from "@phosphor-icons/react";
import { Button } from "@/components/ui/Button";
import { IDENTITY_PLACEMENT } from "@/lib/room-groups";
import type { Combine } from "./useCombine";

function yawOf(combine: Combine, room: string): number {
  const raw = combine.placements[room]?.yawDegrees ?? IDENTITY_PLACEMENT.yawDegrees;
  return ((raw % 360) + 360) % 360;
}

export function CombinePanel({ combine }: { combine: Combine }) {
  const active = combine.activeRoom;

  return (
    <div className="flex flex-col gap-5">
      <div className="px-3">
        <p className="text-ink-muted">
          Drag a room to slide it on the floor. With a keyboard, arrow keys slide the picked room and R turns it.
        </p>
        <div className="mt-4 flex flex-col gap-2" role="group" aria-label="Rooms">
          {combine.rooms.map((room) => (
            <Button
              key={room.name}
              variant="chip"
              aria-pressed={active === room.name}
              onClick={() => combine.setActiveRoom(active === room.name ? null : room.name)}
              className="justify-between"
            >
              <span>{room.name}</span>
              <span className="font-normal text-ink-muted">{yawOf(combine, room.name)}°</span>
            </Button>
          ))}
        </div>

        {active && (
          <div className="mt-4 flex flex-col gap-2 rounded-xl bg-rule/40 p-3">
            <label htmlFor="room-yaw" className="flex items-center justify-between text-sm font-medium">
              <span>Turn {active}</span>
              <span className="text-ink-muted">{yawOf(combine, active)}°</span>
            </label>
            <input
              id="room-yaw"
              type="range"
              min={0}
              max={360}
              step={1}
              value={yawOf(combine, active)}
              onChange={(event) => combine.setYaw(Number(event.target.value))}
              className="w-full accent-ink"
            />
            <div className="flex gap-2">
              <Button disabled={!combine.activeRoom} onClick={() => combine.nudge(0, 0, -15)}>
                <ArrowCounterClockwise size={16} weight="bold" aria-hidden />
              </Button>
              <Button disabled={!combine.activeRoom} onClick={() => combine.nudge(0, 0, 15)}>
                <ArrowClockwise size={16} weight="bold" aria-hidden />
              </Button>
            </div>
          </div>
        )}

        {combine.problem && <p className="mt-3 text-problem">{combine.problem}</p>}

        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="primary" onClick={combine.save} disabled={combine.saving || !combine.hasMoves} className="disabled:opacity-40">
            {combine.saving ? <><CircleNotch size={16} className="animate-spin" aria-hidden /> Saving</> : "Save the alignment"}
          </Button>
          {combine.hasMoves && (
            <Button onClick={combine.reset}>
              <ArrowCounterClockwise size={16} weight="bold" aria-hidden />
              Start over
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
