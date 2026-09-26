"use client";

import { Check, CircleNotch, Cube, DotsThree, DownloadSimple, ImageSquare, Square, SquareHalfBottom, Wheelchair } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { Menu, MENU_ITEM } from "@/components/ui/Menu";
import { textureProgressView, textureStatusView } from "@/lib/texture-status";
import type { TextureStatus } from "@/types/contracts";

export type ViewMode = "overview" | "top";
export type MaterialMode = "reconstructed" | "captured" | "plain" | "coverage" | "scan" | "splat";

const ICON_SIZE = 18;

export type Visibility = {
  cutWalls: boolean;
  onToggleWalls: () => void;
  evidenceAvailable: boolean;
  evidenceShown: boolean;
  onToggleEvidence: () => void;
};

export type Textures = {
  status: TextureStatus | null;
  requesting: boolean;
  error: string | null;
  mode: MaterialMode;
  onMode: (mode: MaterialMode) => void;
  onRequest: () => void;
  reconstruction: { count: number; pending: boolean };
  capturedSplats?: boolean;
};

/** Two or three choices of which exactly one is on, drawn as one pill so they read as a set. */
function Segmented({ children }: { children: ReactNode }) {
  return <div className="flex gap-0.5 rounded-lg bg-ink/[0.05] p-0.5">{children}</div>;
}

function Choice({ pressed, onClick, icon, label, disabled }: { pressed: boolean; onClick: () => void; icon: ReactNode; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium text-ink-muted transition-colors hover:text-ink disabled:opacity-40 aria-pressed:bg-sheet aria-pressed:text-ink aria-pressed:shadow-sm"
    >
      {icon}
      {label}
    </button>
  );
}

/** The photographed look to offer first: the scanned surface when there is one, the photographed boxes otherwise. */
function photoMode(status: TextureStatus | null): MaterialMode | null {
  if (status?.build?.scan_glb_url) return "scan";
  return status?.build ? "captured" : null;
}

function LookChoice({ textures }: { textures: Textures }) {
  const photos = photoMode(textures.status);
  const view = textures.status ? textureStatusView(textures.status) : null;
  if (!photos) return <PhotoRequest textures={textures} />;
  return (
    <Segmented>
      <Choice pressed={textures.mode === photos} onClick={() => textures.onMode(photos)} label="Photos"
        icon={view?.working ? <CircleNotch size={16} className="animate-spin" aria-hidden /> : <ImageSquare size={16} aria-hidden />} />
      <Choice pressed={textures.mode === "plain"} onClick={() => textures.onMode("plain")} label="Plain" icon={<Square size={16} aria-hidden />} />
    </Segmented>
  );
}

/** Before any photo build exists: one plain button that starts one, or what it is waiting for. */
function PhotoRequest({ textures }: { textures: Textures }) {
  if (!textures.status) return null;
  const view = textureStatusView(textures.status);
  if (view.working) {
    return <span role="status" className="flex items-center gap-1.5 px-2 text-sm text-ink-muted"><CircleNotch size={16} className="animate-spin" aria-hidden />Adding photos</span>;
  }
  if (!view.actionLabel) return null;
  return (
    <Button variant="quiet" disabled={textures.requesting} onClick={textures.onRequest}>
      <ImageSquare size={16} aria-hidden />
      {textures.requesting ? "Starting" : "Add photos"}
    </Button>
  );
}

function MenuToggle({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" aria-pressed={on} onClick={onClick} className={MENU_ITEM}>
      <span className="grid size-5 place-items-center">{on && <Check size={16} weight="bold" aria-hidden />}</span>
      {children}
    </button>
  );
}

/** The less-used looks, each offered only where this scan has one to show. */
function extraModes(textures: Textures): { mode: MaterialMode; label: string }[] {
  const build = textures.status?.build;
  const coverage = build ? `Where photos reached (${Math.round(build.coverage.textured_fraction * 100)}%)` : null;
  const candidates: [MaterialMode, string | null][] = [
    ["captured", build?.scan_glb_url ? "Photos on the boxes" : null],
    ["coverage", coverage],
    ["reconstructed", textures.reconstruction.count > 0 ? "Objects rebuilt from photos" : null],
    ["splat", textures.capturedSplats ? "The room rebuilt from photos" : null],
  ];
  return candidates.flatMap(([mode, label]) => (label ? [{ mode, label }] : []));
}

function ModeItems({ textures }: { textures: Textures }) {
  return extraModes(textures).map(({ mode, label }) => (
    <MenuToggle key={mode} on={textures.mode === mode} onClick={() => textures.onMode(mode)}>{label}</MenuToggle>
  ));
}

function DockMenu({ visibility, textures, downloadUrl }: { visibility: Visibility; textures: Textures; downloadUrl: string | null }) {
  return (
    <Menu label="More" icon={<DotsThree size={ICON_SIZE} weight="bold" aria-hidden />} side="above" align="start">
      <MenuToggle on={!visibility.cutWalls} onClick={visibility.onToggleWalls}>Full-height walls</MenuToggle>
      {visibility.evidenceAvailable && (
        <MenuToggle on={visibility.evidenceShown} onClick={visibility.onToggleEvidence}>Scanned surfaces</MenuToggle>
      )}
      <ModeItems textures={textures} />
      {downloadUrl && (
        <a href={downloadUrl} download="room.glb" className={MENU_ITEM}>
          <DownloadSimple size={18} aria-hidden />
          Download the 3D model
        </a>
      )}
    </Menu>
  );
}

type ViewerDockProps = {
  activeMode: ViewMode | null;
  onView: (mode: ViewMode) => void;
  visibility: Visibility;
  textures: Textures;
  downloadUrl: string | null;
  wheelchairMode?: boolean;
  onToggleWheelchair?: () => void;
};

/** Which step a running photo build is on, a bar when the step counts its work, and the time it has left. */
function BuildProgress({ status }: { status: TextureStatus | null }) {
  const view = status?.state === "running" ? textureProgressView(status.progress) : null;
  if (!view) return null;
  return (
    <div className="w-72 max-w-full rounded-xl bg-sheet/95 p-3 shadow-float">
      <p role="status" className="flex items-start gap-1.5 text-sm font-medium text-ink">
        {view.fraction === null && <CircleNotch size={16} className="mt-0.5 shrink-0 animate-spin text-ink-muted" aria-hidden />}
        {view.label}
      </p>
      {view.fraction !== null && (
        <progress
          value={view.fraction}
          max={1}
          aria-label={view.label}
          className="mt-2 block h-1.5 w-full appearance-none overflow-hidden rounded-full bg-ink/[0.08] [&::-moz-progress-bar]:bg-accent [&::-webkit-progress-bar]:bg-transparent [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-accent [&::-webkit-progress-value]:transition-[width] [&::-webkit-progress-value]:duration-700"
        />
      )}
      {view.timeLeft && <p className="mt-1.5 text-sm text-ink-muted">{view.timeLeft}</p>}
    </div>
  );
}

export function ViewerDock({ activeMode, onView, visibility, textures, downloadUrl, wheelchairMode, onToggleWheelchair }: ViewerDockProps) {
  return (
    <div className="absolute bottom-4 left-4 flex max-w-[calc(100%-2rem)] flex-col items-start gap-2">
      <BuildProgress status={textures.status} />
      <Dock>
        <DockControls activeMode={activeMode} onView={onView} visibility={visibility} textures={textures}
          downloadUrl={downloadUrl} wheelchairMode={wheelchairMode} onToggleWheelchair={onToggleWheelchair} />
      </Dock>
    </div>
  );
}

function Dock({ children }: { children: ReactNode }) {
  return <div className="flex max-w-full flex-wrap items-center gap-2 rounded-xl bg-sheet/95 p-1.5 shadow-float">{children}</div>;
}

function DockControls({ activeMode, onView, visibility, textures, downloadUrl, wheelchairMode, onToggleWheelchair }: ViewerDockProps) {
  return (
    <>
      <Segmented>
        <Choice pressed={!wheelchairMode && activeMode === "overview"} onClick={() => onView("overview")} label="3D" icon={<Cube size={16} aria-hidden />} />
        <Choice pressed={!wheelchairMode && activeMode === "top"} onClick={() => onView("top")} label="From above" icon={<SquareHalfBottom size={16} aria-hidden />} />
      </Segmented>
      {onToggleWheelchair && (
        <Choice pressed={Boolean(wheelchairMode)} onClick={onToggleWheelchair} label="Wheelchair view" icon={<Wheelchair size={16} aria-hidden />} />
      )}
      <LookChoice textures={textures} />
      <DockMenu visibility={visibility} textures={textures} downloadUrl={downloadUrl} />
    </>
  );
}
