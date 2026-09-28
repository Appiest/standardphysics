"use client";

import { Armchair, Books, Check, CoatHanger, CookingPot, ShoppingBag, Toilet, X } from "@phosphor-icons/react";
import type { ComponentType } from "react";
import { Button } from "@/components/ui/Button";
import { ActionBar, StepHeading } from "./StepHeading";
import type { Destination } from "@/lib/owner-journey";
import type { PathEditor } from "./usePathEditor";

type IconType = ComponentType<{ size?: number; weight?: "regular" | "bold" | "fill"; "aria-hidden"?: boolean }>;

const PLACES: { place: Destination; name: string; Icon: IconType }[] = [
  { place: "seating", name: "Seats", Icon: Armchair },
  { place: "restroom", name: "Restroom", Icon: Toilet },
  { place: "fitting_room", name: "Fitting room", Icon: CoatHanger },
  { place: "shelves", name: "Shelves", Icon: Books },
  { place: "pickup", name: "Pickup", Icon: ShoppingBag },
];

/** The second check: where else customers go, drawn as a path the owner can drag. */
export function PathStep({ path }: { path: PathEditor }) {
  return (
    <div className="flex min-h-full flex-col gap-6">
      <StepHeading title="Where else do customers go?">Pick every place customers go after the counter. The dashed line is how they walk. Drag a stop on the drawing if it&rsquo;s in the wrong spot.</StepHeading>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Places customers go">
        {PLACES.map(({ place, name, Icon }) => (
          <PlaceChip key={place} name={name} Icon={Icon} on={path.destinations.includes(place)} onToggle={() => path.toggle(place)} />
        ))}
      </div>
      <StaffOnly path={path} />
      {path.problem && <p role="alert" className="text-problem">{path.problem}</p>}
      <ActionBar>
        <Button variant="primary" className="justify-center" disabled={!path.scenario || path.saving} onClick={path.confirm}>
          {path.saving ? "Saving" : "Looks right"}
        </Button>
      </ActionBar>
    </div>
  );
}

/** The kitchen and other staff floor, which the ADA customer checks leave alone. */
function StaffOnly({ path }: { path: PathEditor }) {
  const marked = (path.scenario?.staff_only ?? []).length > 0;
  if (marked) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-pretty text-ink-muted">We skip the shaded staff-only floor, since customers don&rsquo;t go there. Drag it or its corners to cover your kitchen.</p>
        <Button className="self-start" onClick={path.removeStaff}>
          <X size={18} aria-hidden />
          Remove the staff-only area
        </Button>
      </div>
    );
  }
  return (
    <Button className="self-start" disabled={!path.scenario} onClick={() => path.scenario && path.markStaff(middleOf(path.scenario.stops))}>
      <CookingPot size={18} aria-hidden />
      Mark a staff-only area
    </Button>
  );
}

function middleOf(stops: { position: { x: number; y: number } }[]) {
  const x = stops.reduce((sum, stop) => sum + stop.position.x, 0) / stops.length;
  const y = stops.reduce((sum, stop) => sum + stop.position.y, 0) / stops.length;
  return { x, y };
}

function PlaceChip({ name, Icon, on, onToggle }: { name: string; Icon: IconType; on: boolean; onToggle: () => void }) {
  return (
    <Button variant="chip" aria-pressed={on} onClick={onToggle} className="min-h-11">
      {on ? <Check size={18} weight="bold" aria-hidden /> : <Icon size={18} aria-hidden />}
      {name}
    </Button>
  );
}
