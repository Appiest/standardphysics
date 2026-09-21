"use client";

import { ChartPieSlice, CircleNotch, Cube, CubeTransparent, DownloadSimple, ImageSquare, Scan, Shapes, Square, SquareHalfBottom, Wall, Wheelchair } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { IconButton, IconLink } from "@/components/ui/IconButton";
import { textureStatusView } from "@/lib/texture-status";
import type { TextureStatus } from "@/types/contracts";

export type ViewMode = "overview" | "top";
export type MaterialMode = "reconstructed" | "captured" | "plain" | "coverage" | "scan";

const ICON_SIZE = 18;

function DockGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="flex items-center gap-0.5">
      {children}
    </div>
  );
}

function CameraGroup({
  activeMode,
  onView,
  wheelchairMode,
  onToggleWheelchair,
}: {
  activeMode: ViewMode | null;
  onView: (mode: ViewMode) => void;
  wheelchairMode?: boolean;
  onToggleWheelchair?: () => void;
}) {
  return (
    <DockGroup label="Camera">
      <IconButton label="Whole shop" aria-pressed={!wheelchairMode && activeMode === "overview"} onClick={() => onView("overview")}>
        <Cube size={ICON_SIZE} aria-hidden />
      </IconButton>
      <IconButton label="From above" aria-pressed={!wheelchairMode && activeMode === "top"} onClick={() => onView("top")}>
        <SquareHalfBottom size={ICON_SIZE} aria-hidden />
      </IconButton>
      {onToggleWheelchair && (
        <IconButton
          label={wheelchairMode ? "Exit wheelchair view" : "Wheelchair navigation (seated eye height 1.15 m)"}
          aria-pressed={wheelchairMode}
          onClick={onToggleWheelchair}
        >
          <Wheelchair size={ICON_SIZE} weight={wheelchairMode ? "bold" : "regular"} aria-hidden />
        </IconButton>
      )}
    </DockGroup>
  );
}

export type Visibility = {
  cutWalls: boolean;
  onToggleWalls: () => void;
  evidenceAvailable: boolean;
  evidenceShown: boolean;
  onToggleEvidence: () => void;
};

function VisibilityGroup({ visibility }: { visibility: Visibility }) {
  return (
    <DockGroup label="Show">
      <IconButton label={visibility.cutWalls ? "Show full walls" : "Cut walls down"} aria-pressed={!visibility.cutWalls} onClick={visibility.onToggleWalls}>
        <Wall size={ICON_SIZE} aria-hidden />
      </IconButton>
      {visibility.evidenceAvailable && (
        <IconButton label={visibility.evidenceShown ? "Hide scanned surfaces" : "Show scanned surfaces"} aria-pressed={visibility.evidenceShown} onClick={visibility.onToggleEvidence}>
          <CubeTransparent size={ICON_SIZE} aria-hidden />
        </IconButton>
      )}
    </DockGroup>
  );
}

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

function coverageLabel(status: TextureStatus) {
  const fraction = status.build?.coverage.textured_fraction;
  return fraction === undefined ? "Photo coverage" : `Photo coverage, ${Math.round(fraction * 100)}% photographed`;
}

function TextureAction({ textures, actionLabel }: { textures: Textures; actionLabel: string | null }) {
  if (!actionLabel) return null;
  return (
    <Button variant="chip" disabled={textures.requesting} onClick={textures.onRequest} className="shadow-none">
      <ImageSquare size={16} aria-hidden />
      {textures.requesting ? "Starting textures" : actionLabel}
    </Button>
  );
}

function photoLabel(textures: Textures, status: TextureStatus, statusMessage: string | null) {
  const problem = textures.error ?? status.error;
  if (problem) return problem;
  if (status.build === null && statusMessage) return statusMessage;
  return "Photo textures";
}

function BuiltModes({ textures, status }: { textures: Textures; status: TextureStatus }) {
  return (
    <>
      {status.build?.scan_glb_url && !textures.capturedSplats && (
        <IconButton
          label="The room as it was scanned"
          aria-pressed={textures.mode === "scan"}
          onClick={() => textures.onMode("scan")}
        >
          <Scan size={ICON_SIZE} aria-hidden />
        </IconButton>
      )}
      <IconButton label="Plain materials" aria-pressed={textures.mode === "plain"} onClick={() => textures.onMode("plain")}>
        <Square size={ICON_SIZE} aria-hidden />
      </IconButton>
      <IconButton label={coverageLabel(status)} aria-pressed={textures.mode === "coverage"} onClick={() => textures.onMode("coverage")}>
        <ChartPieSlice size={ICON_SIZE} aria-hidden />
      </IconButton>
    </>
  );
}

function ReconstructedMode({ textures }: { textures: Textures }) {
  const { count, pending } = textures.reconstruction;
  if (count === 0) return null;
  const label = pending ? "Updating reconstructed objects from photos" : `Reconstructed objects, ${count} inferred from photos`;
  return (
    <>
      <IconButton label={label} aria-pressed={textures.mode === "reconstructed"} onClick={() => textures.onMode("reconstructed")}>
        {pending ? <CircleNotch size={ICON_SIZE} className="animate-spin" aria-hidden /> : <Shapes size={ICON_SIZE} aria-hidden />}
      </IconButton>
      {pending && <span className="sr-only" role="status">{label}</span>}
    </>
  );
}

function PhotoModes({ textures, status }: { textures: Textures; status: TextureStatus }) {
  const view = textureStatusView(status);
  const hasBuild = status.build !== null;
  return (
    <>
      <IconButton
        label={photoLabel(textures, status, view.message)}
        aria-pressed={hasBuild && textures.mode === "captured"}
        aria-disabled={!hasBuild}
        onClick={() => hasBuild && textures.onMode("captured")}
      >
        {view.working ? <CircleNotch size={ICON_SIZE} className="animate-spin" aria-hidden /> : <ImageSquare size={ICON_SIZE} aria-hidden />}
      </IconButton>
      {hasBuild && <BuiltModes textures={textures} status={status} />}
      <TextureAction textures={textures} actionLabel={view.actionLabel} />
      {view.working && <span className="sr-only" role="status">{view.message}</span>}
    </>
  );
}

function MaterialGroup({ textures }: { textures: Textures }) {
  const { status } = textures;
  if (!status && textures.reconstruction.count === 0 && !textures.capturedSplats) return null;
  return (
    <DockGroup label="Materials">
      {textures.capturedSplats && <IconButton label="Photographic preview" aria-pressed={textures.mode === "scan"} onClick={() => textures.onMode("scan")}><Scan size={ICON_SIZE} aria-hidden /></IconButton>}
      <ReconstructedMode textures={textures} />
      {status && <PhotoModes textures={textures} status={status} />}
    </DockGroup>
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

export function ViewerDock({ activeMode, onView, visibility, textures, downloadUrl, wheelchairMode, onToggleWheelchair }: ViewerDockProps) {
  return (
    <div className="absolute bottom-4 left-4 flex max-w-[calc(100%-2rem)] flex-wrap items-center gap-3 rounded-xl bg-sheet/95 p-1 shadow-float">
      <CameraGroup activeMode={activeMode} onView={onView} wheelchairMode={wheelchairMode} onToggleWheelchair={onToggleWheelchair} />
      <VisibilityGroup visibility={visibility} />
      <MaterialGroup textures={textures} />
      {downloadUrl && (
        <IconLink label="Export GLB" href={downloadUrl} download="room.glb">
          <DownloadSimple size={ICON_SIZE} aria-hidden />
        </IconLink>
      )}
    </div>
  );
}
