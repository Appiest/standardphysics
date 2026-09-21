"use client";

import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowsIn,
  ArrowUp,
  Compass,
  Crosshair,
  Speedometer,
  Wheelchair,
  X,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, type PointerEvent } from "react";
import { Button } from "@/components/ui/Button";
import {
  ADA_RULES,
  boundsEstimate,
  INCHES_PER_METER,
  inspectionCategory,
  inspectionCategoryLabel,
  inspectionLimitations,
  isDockableInspectionTarget,
} from "@/lib/wheelchair-inspection";
import { WHEELCHAIR_PROFILE_LIMITS, type WheelchairProfile } from "@/lib/wheelchair-motion";
import type { SceneNode } from "@/types/contracts";
import type { WheelchairState } from "./WheelchairController";

type WheelchairHudProps = {
  active: boolean;
  state: WheelchairState | null;
  onExit: () => void;
  onDock: (node: SceneNode) => void;
  onSelectNode: (nodeId: string) => void;
  profile: WheelchairProfile;
  onProfileChange: (profile: WheelchairProfile) => void;
};

function dispatchKeyEvent(code: string, type: "keydown" | "keyup") {
  window.dispatchEvent(
    new KeyboardEvent(type, {
      code,
      bubbles: true,
      cancelable: true,
    }),
  );
}

/** A short pointer activation should move once; a held activation already moves every frame. */
export const POINTER_TAP_MAX_MS = 80;
export const KEY_TAP_HOLD_MS = 120;

export function shouldTapAfterPointerPress(durationMs: number): boolean {
  return Number.isFinite(durationMs) && durationMs < POINTER_TAP_MAX_MS;
}

function dispatchKeyTap(code: string, releaseAfterMs: number, onRelease: () => void): number {
  dispatchKeyEvent(code, "keydown");
  return window.setTimeout(() => {
    dispatchKeyEvent(code, "keyup");
    onRelease();
  }, releaseAfterMs);
}

function VirtualButton({
  code,
  label,
  children,
  className = "",
}: {
  code: string;
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  const activePointerId = useRef<number | null>(null);
  const pointerDownAt = useRef<number | null>(null);
  const tapAfterPointerPress = useRef<boolean | null>(null);
  const tapReleaseTimer = useRef<number | null>(null);

  const clearTap = useCallback(() => {
    if (tapReleaseTimer.current === null) return;
    window.clearTimeout(tapReleaseTimer.current);
    tapReleaseTimer.current = null;
    dispatchKeyEvent(code, "keyup");
  }, [code]);

  const tap = useCallback(() => {
    clearTap();
    tapReleaseTimer.current = dispatchKeyTap(code, KEY_TAP_HOLD_MS, () => {
      tapReleaseTimer.current = null;
    });
  }, [clearTap, code]);

  const handleDown = useCallback((event: PointerEvent<HTMLButtonElement>) => {
    clearTap();
    activePointerId.current = event.pointerId;
    pointerDownAt.current = performance.now();
    tapAfterPointerPress.current = null;
    event.currentTarget.setPointerCapture(event.pointerId);
    dispatchKeyEvent(code, "keydown");
  }, [clearTap, code]);

  const handleUp = useCallback((event: PointerEvent<HTMLButtonElement>, allowTap: boolean) => {
    if (activePointerId.current !== event.pointerId) return;
    const duration = pointerDownAt.current === null ? Number.POSITIVE_INFINITY : performance.now() - pointerDownAt.current;
    activePointerId.current = null;
    pointerDownAt.current = null;
    tapAfterPointerPress.current = allowTap && shouldTapAfterPointerPress(duration);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dispatchKeyEvent(code, "keyup");
  }, [code]);

  const handlePointerUp = useCallback((event: PointerEvent<HTMLButtonElement>) => {
    handleUp(event, true);
  }, [handleUp]);

  const handlePointerCancel = useCallback((event: PointerEvent<HTMLButtonElement>) => {
    handleUp(event, false);
  }, [handleUp]);

  const handleClick = useCallback(() => {
    const pointerTap = tapAfterPointerPress.current;
    tapAfterPointerPress.current = null;
    // Pointer presses that lasted long enough to drive already supplied their movement.
    // A click without a pointer (keyboard or assistive technology) still gets one pulse.
    if (pointerTap === false) return;
    tap();
  }, [tap]);

  useEffect(() => () => {
    clearTap();
    if (activePointerId.current !== null) dispatchKeyEvent(code, "keyup");
  }, [clearTap, code]);

  return (
    <button
      type="button"
      aria-label={label}
      onPointerDown={handleDown}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerCancel}
      onLostPointerCapture={handlePointerUp}
      onPointerLeave={handlePointerUp}
      onClick={handleClick}
      className={`flex h-10 w-10 touch-none select-none items-center justify-center rounded-lg bg-sheet/95 text-ink shadow-sm transition-colors hover:bg-sheet active:bg-ink active:text-sheet ${className}`}
    >
      {children}
    </button>
  );
}

function ScanInspection({
  node,
  distance,
}: {
  node: SceneNode;
  distance: number;
}) {
  const category = inspectionCategory(node);
  const dimensions = boundsEstimate(node);
  const distanceInches = distance * INCHES_PER_METER;
  const limitations = inspectionLimitations(category);

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-rule/60 bg-sheet/95 p-2.5 text-xs">
      <div className="flex items-center justify-between font-semibold text-ink">
        <span>LiDAR scan inspection</span>
        <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-sky-700">
          Estimate only
        </span>
      </div>

      <p className="text-ink-muted">{inspectionCategoryLabel(category)}</p>

      <div className="rounded bg-sheet/70 p-2 text-ink-muted">
        <p>Object bounds estimate: {dimensions.widthInches.toFixed(0)}″ W × {dimensions.depthInches.toFixed(0)}″ D × {dimensions.heightInches.toFixed(0)}″ H</p>
        <p className="mt-1">Horizontal center distance: {distanceInches.toFixed(0)}″ ({distance.toFixed(2)} m)</p>
        <p className="mt-1 text-[10px]">Target-center geometry only; not a reach measurement.</p>
      </div>

      <div>
        <p className="font-semibold text-ink">Not measured from this scan</p>
        <ul className="mt-1 space-y-0.5 text-ink-muted">
          {limitations.map((limitation) => <li key={limitation}>• {limitation}</li>)}
        </ul>
      </div>

      <div className="border-t border-rule/60 pt-2">
        <p className="font-semibold text-ink">Rules that may apply</p>
        <ul className="mt-1 space-y-1 text-[10px] text-ink-muted">
          {ADA_RULES.map((rule) => (
            <li key={rule.section}>
              <a className="font-semibold text-sky-700 underline" href={rule.url} rel="noreferrer" target="_blank">§{rule.section} {rule.label}</a>: {rule.caveat}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function ProfileSlider({
  label,
  value,
  unit,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  unit: string;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="grid grid-cols-[1fr_auto] items-center gap-x-2 gap-y-1 text-[11px] text-ink-muted">
      <span>{label}</span>
      <span className="font-mono text-ink">{value.toFixed(2)} {unit}</span>
      <input
        className="col-span-2 w-full accent-sky-600"
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
      />
    </label>
  );
}

function ProfileControls({ profile, onChange }: { profile: WheelchairProfile; onChange: (profile: WheelchairProfile) => void }) {
  return (
    <details className="pointer-events-auto rounded-lg border border-rule/60 bg-sheet/95 px-2.5 py-2 text-xs text-ink-muted shadow-sm">
      <summary className="cursor-pointer font-semibold text-ink">Adjust navigation estimates</summary>
      <div className="mt-2 grid gap-2">
        <ProfileSlider label="Seated eye height" value={profile.eyeHeight} unit="m" {...WHEELCHAIR_PROFILE_LIMITS.eyeHeight} step={0.01} onChange={(eyeHeight) => onChange({ ...profile, eyeHeight })} />
        <ProfileSlider label="Movement speed" value={profile.speed} unit="m/s" {...WHEELCHAIR_PROFILE_LIMITS.speed} step={0.1} onChange={(speed) => onChange({ ...profile, speed })} />
        <ProfileSlider label="Collision radius" value={profile.collisionRadius} unit="m" {...WHEELCHAIR_PROFILE_LIMITS.collisionRadius} step={0.01} onChange={(collisionRadius) => onChange({ ...profile, collisionRadius })} />
      </div>
      <p className="mt-2 text-[10px] leading-snug">Adjustable estimates. Movement uses a conservative circular footprint, not an exact wheelchair model or an ADA compliance check.</p>
    </details>
  );
}

// eslint-disable-next-line complexity
export function WheelchairHud({
  active,
  state,
  onExit,
  onDock,
  onSelectNode,
  profile,
  onProfileChange,
}: WheelchairHudProps) {
  if (!active) return null;

  const node = state?.targetedNode ?? null;
  const reachDistance = state?.reachDistance ?? 0;
  const speed = state?.speed ?? 0;
  const yaw = state ? Math.round(((state.yaw % 360) + 360) % 360) : 0;
  const x = state?.x.toFixed(1) ?? "0.0";
  const z = state?.z.toFixed(1) ?? "0.0";

  const isDockable = node && isDockableInspectionTarget(inspectionCategory(node));

  return (
    <div className="pointer-events-none absolute inset-0 z-30 flex flex-col justify-between p-4">
      {/* Top Bar Overlay */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Left: Mode Badge & Live Telemetrics */}
        <div className="pointer-events-auto flex flex-wrap items-center gap-2 rounded-xl bg-sheet/95 p-1.5 shadow-float backdrop-blur">
          <div className="flex items-center gap-2 rounded-lg bg-sky-500/15 px-3 py-1.5 text-sky-800">
            <Wheelchair size={20} weight="bold" className="shrink-0" />
            <div className="text-left">
              <p className="text-xs font-bold leading-none">Wheelchair View</p>
              <p className="text-[10px] text-sky-700">Seated eye height {profile.eyeHeight.toFixed(2)} m ({(profile.eyeHeight * INCHES_PER_METER).toFixed(0)}″), adjustable estimate</p>
            </div>
          </div>

          <div className="hidden items-center gap-3 px-2 font-mono text-xs text-ink-muted sm:flex">
            <div className="flex items-center gap-1">
              <Compass size={14} className="text-ink-muted" />
              <span>{yaw}°</span>
            </div>
            <div className="flex items-center gap-1">
              <Speedometer size={14} className="text-ink-muted" />
              <span>{speed.toFixed(1)} m/s</span>
            </div>
            <div className="flex items-center gap-1 border-l border-rule pl-2">
              <span>X:{x}m</span>
              <span>Z:{z}m</span>
            </div>
          </div>
        </div>

        <ProfileControls profile={profile} onChange={onProfileChange} />

        {/* Right: Exit Wheelchair View */}
        <div className="pointer-events-auto flex items-center gap-2 rounded-xl bg-sheet/95 p-1 shadow-float backdrop-blur">
          <Button
            variant="quiet"
            onClick={onExit}
            className="flex items-center gap-1.5 text-xs font-semibold"
          >
            <X size={15} weight="bold" />
            <span>Exit (Esc)</span>
          </Button>
        </div>
      </div>

      {/* Middle/Bottom Interactive Area */}
      <div className="flex flex-col-reverse items-end justify-between gap-4 md:flex-row md:items-end">
        {/* Bottom Left: Keyboard Hints */}
        <div className="pointer-events-auto hidden rounded-xl bg-sheet/95 p-2.5 text-xs text-ink-muted shadow-float backdrop-blur md:block">
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-ink">
            Wheelchair Controls
          </p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[11px]">
            <div>
              <kbd className="rounded bg-rule/70 px-1 py-0.5 text-ink">W / ↑</kbd> Drive
              Forward
            </div>
            <div>
              <kbd className="rounded bg-rule/70 px-1 py-0.5 text-ink">A / ←</kbd> Steer Left
            </div>
            <div>
              <kbd className="rounded bg-rule/70 px-1 py-0.5 text-ink">S / ↓</kbd> Reverse
            </div>
            <div>
              <kbd className="rounded bg-rule/70 px-1 py-0.5 text-ink">D / →</kbd> Steer Right
            </div>
            <div>
              <kbd className="rounded bg-rule/70 px-1 py-0.5 text-ink">Q / E</kbd> Strafe
            </div>
            <div>
              <kbd className="rounded bg-rule/70 px-1 py-0.5 text-ink">Esc</kbd> Exit View
            </div>
          </div>
        </div>

        {/* Center: In-Range Object Card */}
        {node && (
          <div className="pointer-events-auto max-w-sm rounded-xl border border-sky-400/30 bg-sheet/95 p-3.5 shadow-float backdrop-blur">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div>
                <div className="flex items-center gap-1.5">
                  <Crosshair size={16} weight="bold" className="text-sky-600" />
                  <h3 className="text-sm font-bold text-ink">{node.label}</h3>
                </div>
                <p className="text-xs text-ink-muted capitalize">
                  LiDAR object bounds estimate •{" "}
                  {(node.dimensions.x * INCHES_PER_METER).toFixed(0)}″W ×{" "}
                  {(node.dimensions.y * INCHES_PER_METER).toFixed(0)}″D ×{" "}
                  {(node.dimensions.z * INCHES_PER_METER).toFixed(0)}″H
                </p>
              </div>

              <span className="rounded bg-sky-100 px-2 py-0.5 text-[11px] font-semibold text-sky-800">
                {(reachDistance * INCHES_PER_METER).toFixed(0)}″ center distance
              </span>
            </div>

            <ScanInspection node={node} distance={reachDistance} />

            {/* Object Actions */}
            <div className="mt-3 flex items-center gap-2">
              {isDockable && (
                <Button
                  variant="primary"
                  onClick={() => onDock(node)}
                  className="flex-1 text-xs"
                >
                  <ArrowsIn size={15} weight="bold" />
                  Position near object
                </Button>
              )}
              <Button
                variant="quiet"
                onClick={() => onSelectNode(node.id)}
                className="text-xs"
              >
                Inspect
              </Button>
            </div>
          </div>
        )}

        {/* Bottom Right: On-screen Touch/Mouse Steering Pad */}
        <div className="pointer-events-auto flex flex-col items-center gap-1 rounded-2xl bg-sheet/95 p-2 shadow-float backdrop-blur">
          <p className="text-[10px] font-bold uppercase tracking-wider text-ink-muted">
            Steering
          </p>
          <div className="flex gap-1">
            <VirtualButton code="KeyQ" label="Strafe Left" className="text-xs font-bold">
              Q
            </VirtualButton>
            <VirtualButton code="KeyW" label="Move Forward">
              <ArrowUp size={18} weight="bold" />
            </VirtualButton>
            <VirtualButton code="KeyE" label="Strafe Right" className="text-xs font-bold">
              E
            </VirtualButton>
          </div>
          <div className="flex gap-1">
            <VirtualButton code="KeyA" label="Steer Left">
              <ArrowLeft size={18} weight="bold" />
            </VirtualButton>
            <VirtualButton code="KeyS" label="Move Backward">
              <ArrowDown size={18} weight="bold" />
            </VirtualButton>
            <VirtualButton code="KeyD" label="Steer Right">
              <ArrowRight size={18} weight="bold" />
            </VirtualButton>
          </div>
        </div>
      </div>
    </div>
  );
}
