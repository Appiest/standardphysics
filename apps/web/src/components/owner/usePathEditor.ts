"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { confirmRoute } from "@/lib/layout-client";
import { suggestPath, walkingRoute } from "@/lib/owner-client";
import type { Destination } from "@/lib/owner-journey";
import { moveMarker, type StopMarker, stopMarkers } from "@/lib/route";
import type { Scenario, Vec3 } from "@/types/contracts";

/**
 * The customer path while the owner shapes it: the places they picked, the
 * stops they dragged, and the route a customer would walk between them, which
 * is the same route the checks measure.
 */
export function usePathEditor(scanId: string, starting: Scenario | null, picked: Destination[]) {
  const router = useRouter();
  const [scenario, setScenario] = useState<Scenario | null>(starting);
  const [destinations, setDestinations] = useState<Destination[]>(picked);
  const [touched, setTouched] = useState(false);
  const [startingBefore, setStartingBefore] = useState(starting);
  const [pickedBefore, setPickedBefore] = useState(picked.join(","));
  const [legs, setLegs] = useState<Vec3[][]>([]);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const latest = useRef(0);
  const routeAsked = useRef(0);
  const markers = useMemo(() => (scenario ? stopMarkers(scenario) : []), [scenario]);

  // The page loads before the owner reaches this step, and marking the counter
  // changes where the path should start. Take each newer suggestion from the
  // server until the owner starts shaping the path themselves.
  if (!touched && starting !== startingBefore) {
    setStartingBefore(starting);
    if (starting) setScenario(starting);
  }
  if (!touched && picked.join(",") !== pickedBefore) {
    setPickedBefore(picked.join(","));
    setDestinations(picked);
  }

  const route = useCallback((next: Scenario | null) => {
    if (!next) return;
    const asked = ++routeAsked.current;
    walkingRoute(scanId, next)
      .then((walked) => { if (asked === routeAsked.current) setLegs(walked.legs.map((leg) => leg.path)); })
      .catch(() => { if (asked === routeAsked.current) setLegs([]); });
  }, [scanId]);

  useEffect(() => { route(startingBefore); }, [startingBefore, route]);

  const toggle = useCallback((place: Destination) => {
    const next = destinations.includes(place) ? destinations.filter((each) => each !== place) : [...destinations, place];
    setDestinations(next);
    setTouched(true);
    const asked = ++latest.current;
    suggestPath(scanId, next)
      .then((suggested) => {
        if (asked !== latest.current) return;
        setScenario(suggested);
        route(suggested);
      })
      .catch(() => { if (asked === latest.current) setProblem("We couldn't draw that path. Try again."); });
  }, [destinations, scanId, route]);

  const drag = useCallback((marker: StopMarker, dx: number, dy: number) => {
    setTouched(true);
    setLegs([]);
    setScenario((current) => (current ? moveMarker(current, marker, dx, dy) : current));
  }, []);

  const settle = useCallback(() => route(scenario), [route, scenario]);

  const confirm = useCallback(async () => {
    if (!scenario) return;
    setSaving(true);
    setProblem(null);
    try {
      await confirmRoute(scanId, scenario);
      router.refresh();
    } catch {
      setProblem("We couldn't save the path. Try again.");
      setSaving(false);
    }
  }, [scanId, scenario, router]);

  return { scenario, markers, legs, destinations, saving, problem, toggle, drag, settle, confirm };
}

export type PathEditor = ReturnType<typeof usePathEditor>;
