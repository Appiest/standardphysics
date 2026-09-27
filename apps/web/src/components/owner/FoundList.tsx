"use client";

import { CaretDown, CashRegister, Chair, Check, Cube, FireExtinguisher, SidebarSimple } from "@phosphor-icons/react";
import { type ComponentType, useId, useState } from "react";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { foldedSummary, type FoundGroup, type FoundGroupId, type FoundRow, heightRange, rowLabel } from "@/lib/found-objects";
import type { FoundObjects } from "./useFoundObjects";

type IconType = ComponentType<{ size?: number; weight?: "regular" | "bold"; className?: string; "aria-hidden"?: boolean }>;

const GROUP_ICONS: Record<FoundGroupId, IconType> = {
  service: CashRegister,
  seating: Chair,
  safety: FireExtinguisher,
  other: Cube,
};

type ListProps = Pick<FoundObjects, "groups" | "hoveredRowId" | "selectedRowId" | "hoverRow" | "toggleRow">;
type LegendProps = ListProps & Pick<FoundObjects, "legendOpen" | "setLegendOpen">;

function RowButton({ row, hovered, selected, onHover, onToggle }: { row: FoundRow; hovered: boolean; selected: boolean; onHover: (rowId: string | null) => void; onToggle: (rowId: string) => void }) {
  const height = heightRange(row.topInches);
  const tone = selected ? "bg-accent/10 ring-2 ring-inset ring-accent" : hovered ? "bg-ink/5" : "hover:bg-ink/5";
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={() => onToggle(row.id)}
      onPointerEnter={() => onHover(row.id)}
      onPointerLeave={() => onHover(null)}
      onFocus={() => onHover(row.id)}
      onBlur={() => onHover(null)}
      className={`flex min-h-10 w-full items-center gap-3 rounded-lg px-2.5 py-1.5 text-left transition-colors duration-150 ${tone}`}
    >
      <span className="min-w-0 flex-1 text-pretty">{rowLabel(row)}</span>
      {height && (
        <span className="shrink-0 text-sm text-ink-muted">
          top <span className="measurement text-ink">{height}</span>
        </span>
      )}
      <Check size={16} weight="bold" aria-hidden className={`shrink-0 text-accent ${selected ? "" : "invisible"}`} />
    </button>
  );
}

function GroupSection({ group, list }: { group: FoundGroup; list: ListProps }) {
  const headingId = useId();
  const Icon = GROUP_ICONS[group.id];
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-1">
      <h3 id={headingId} className="flex items-center gap-2 px-2.5 text-sm font-medium text-ink-muted">
        <Icon size={16} aria-hidden />
        {group.title}
      </h3>
      <ul className="flex flex-col">
        {group.rows.map((row) => (
          <li key={row.id}>
            <RowButton row={row} hovered={row.id === list.hoveredRowId} selected={row.id === list.selectedRowId} onHover={list.hoverRow} onToggle={list.toggleRow} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The long tail stays folded until asked for, unless the owner already picked something in it. */
function OtherGroup({ group, list }: { group: FoundGroup; list: ListProps }) {
  const holdsSelection = group.rows.some((row) => row.id === list.selectedRowId);
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const shown = open || holdsSelection;
  return (
    <div className="flex flex-col gap-1">
      <Button aria-expanded={shown} aria-controls={panelId} className="self-start" onClick={() => setOpen(!shown)}>
        <CaretDown size={14} weight="bold" aria-hidden className={`transition-transform duration-150 ${shown ? "rotate-180" : ""}`} />
        {shown ? "Hide the other things" : `Show ${foldedSummary(group.rows)}`}
      </Button>
      <div id={panelId} hidden={!shown}>
        <GroupSection group={group} list={list} />
      </div>
    </div>
  );
}

function Groups({ list }: { list: ListProps }) {
  return (
    <>
      {list.groups.map((group) => group.id === "other"
        ? <OtherGroup key={group.id} group={group} list={list} />
        : <GroupSection key={group.id} group={group} list={list} />)}
    </>
  );
}

/** The found pieces below the step on a phone, where the model sits above and lights up as rows are tapped. */
export function FoundSection({ list, shown }: { list: ListProps; shown: boolean }) {
  const headingId = useId();
  if (!shown || list.groups.length === 0) return null;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3 lg:hidden">
      <h2 id={headingId} className="text-lg font-semibold">What we found</h2>
      <Groups list={list} />
    </section>
  );
}

/** The found pieces as a legend over the model on a wide screen, folded away to a single button when not wanted. */
export function FoundLegend({ list, shown }: { list: LegendProps; shown: boolean }) {
  const headingId = useId();
  if (!shown || list.groups.length === 0) return null;
  const setOpen = list.setLegendOpen;
  if (!list.legendOpen) {
    return (
      <Button variant="chip" className="absolute left-4 top-4 hidden lg:inline-flex" onClick={() => setOpen(true)}>
        <SidebarSimple size={18} aria-hidden />
        Show what we found
      </Button>
    );
  }
  return (
    <section aria-labelledby={headingId} className="absolute left-4 top-4 hidden max-h-[calc(100%-2rem)] w-80 flex-col rounded-2xl bg-sheet/90 shadow-float backdrop-blur-sm lg:flex">
      <header className="flex items-center justify-between gap-2 py-2 pe-2 ps-5">
        <h2 id={headingId} className="text-lg font-semibold">What we found</h2>
        <IconButton label="Hide the list" tooltipSide="below" onClick={() => setOpen(false)}>
          <SidebarSimple size={18} aria-hidden />
        </IconButton>
      </header>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto overscroll-contain px-2.5 pb-4">
        <Groups list={list} />
      </div>
    </section>
  );
}
