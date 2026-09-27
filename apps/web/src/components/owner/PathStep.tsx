"use client";

import { Armchair, Books, Check, CoatHanger, ShoppingBag, Toilet } from "@phosphor-icons/react";
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
      {path.problem && <p role="alert" className="text-problem">{path.problem}</p>}
      <ActionBar>
        <Button variant="primary" className="justify-center" disabled={!path.scenario || path.saving} onClick={path.confirm}>
          {path.saving ? "Saving" : "Looks right"}
        </Button>
      </ActionBar>
    </div>
  );
}

function PlaceChip({ name, Icon, on, onToggle }: { name: string; Icon: IconType; on: boolean; onToggle: () => void }) {
  return (
    <Button variant="chip" aria-pressed={on} onClick={onToggle} className="min-h-11">
      {on ? <Check size={18} weight="bold" aria-hidden /> : <Icon size={18} aria-hidden />}
      {name}
    </Button>
  );
}
