"use client";

import { useCallback, useSyncExternalStore } from "react";

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function read(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === "seen";
  } catch {
    return true;
  }
}

/**
 * Whether this browser has already shown a one-time card, like the first
 * results tip. The server renders it as seen, so a card that was dismissed never
 * flashes back while the page loads. A private window that refuses storage
 * counts as seen, because a tip that returns on every visit is worse than none.
 */
export function useSeenOnce(key: string): [boolean, () => void] {
  const seen = useSyncExternalStore(subscribe, () => read(key), () => true);
  const markSeen = useCallback(() => {
    try {
      window.localStorage.setItem(key, "seen");
    } catch {
      // Nothing to remember it with.
    }
    listeners.forEach((listener) => listener());
  }, [key]);
  return [seen, markSeen];
}
