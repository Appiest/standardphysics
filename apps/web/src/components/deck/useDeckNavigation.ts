"use client";

import { useCallback, useEffect, useRef, useState, type MouseEvent, type PointerEvent } from "react";

type DeckPosition = { index: number; step: number; direction: 1 | -1 };

const SWIPE_DISTANCE_PX = 60;

function clampIndex(index: number, slideCount: number) {
  return Math.min(Math.max(index, 0), slideCount - 1);
}

function slideIndexFromHash(slideCount: number) {
  const slideNumber = Number.parseInt(window.location.hash.slice(1), 10);
  return Number.isFinite(slideNumber) ? clampIndex(slideNumber - 1, slideCount) : 0;
}

function moveTo(current: DeckPosition, requested: number, slideCount: number, step = 0): DeckPosition {
  const index = clampIndex(requested, slideCount);
  if (index === current.index) return current;
  return { index, step, direction: index > current.index ? 1 : -1 };
}

function advance(current: DeckPosition, stepCounts: number[]): DeckPosition {
  if (current.step < stepCounts[current.index] - 1) return { ...current, step: current.step + 1, direction: 1 };
  return moveTo(current, current.index + 1, stepCounts.length);
}

function retreat(current: DeckPosition, stepCounts: number[]): DeckPosition {
  if (current.step > 0) return { ...current, step: current.step - 1, direction: -1 };
  const previous = clampIndex(current.index - 1, stepCounts.length);
  return moveTo(current, previous, stepCounts.length, stepCounts[previous] - 1);
}

export function useDeckNavigation(stepCounts: number[]) {
  const slideCount = stepCounts.length;
  const [position, setPosition] = useState<DeckPosition>(() => ({
    index: slideIndexFromHash(slideCount),
    step: 0,
    direction: 1,
  }));
  const pointerStart = useRef<number | null>(null);

  const goTo = useCallback(
    (index: number) => setPosition((current) => moveTo(current, index, slideCount)),
    [slideCount],
  );
  const step = useCallback(
    (offset: 1 | -1) => setPosition((current) => (offset === 1 ? advance(current, stepCounts) : retreat(current, stepCounts))),
    [stepCounts],
  );

  useEffect(() => {
    window.history.replaceState(null, "", `#${position.index + 1}`);
  }, [position.index]);

  useEffect(() => {
    const followHash = () => goTo(slideIndexFromHash(slideCount));
    window.addEventListener("hashchange", followHash);
    return () => window.removeEventListener("hashchange", followHash);
  }, [goTo, slideCount]);

  useEffect(() => {
    const actions: Record<string, () => void> = {
      ArrowRight: () => step(1),
      ArrowDown: () => step(1),
      PageDown: () => step(1),
      " ": () => step(1),
      Enter: () => step(1),
      ArrowLeft: () => step(-1),
      ArrowUp: () => step(-1),
      PageUp: () => step(-1),
      Backspace: () => step(-1),
      Home: () => goTo(0),
      End: () => goTo(slideCount - 1),
    };

    function handleKeyDown(event: KeyboardEvent) {
      const action = actions[event.key];
      if (!action || event.metaKey || event.ctrlKey || event.altKey) return;
      event.preventDefault();
      action();
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [goTo, step, slideCount]);

  const handlePointerDown = useCallback((event: PointerEvent) => {
    pointerStart.current = event.button === 0 ? event.clientX : null;
  }, []);

  const handlePointerUp = useCallback(
    (event: PointerEvent) => {
      const startX = pointerStart.current;
      pointerStart.current = null;
      if (startX === null) return;
      const travel = event.clientX - startX;
      const isSwipe = Math.abs(travel) >= SWIPE_DISTANCE_PX;
      step(isSwipe && travel > 0 ? -1 : 1);
    },
    [step],
  );

  const handleContextMenu = useCallback(
    (event: MouseEvent) => {
      event.preventDefault();
      step(-1);
    },
    [step],
  );

  const advanceOneStep = useCallback(() => step(1), [step]);

  return { ...position, advance: advanceOneStep, handlePointerDown, handlePointerUp, handleContextMenu };
}
