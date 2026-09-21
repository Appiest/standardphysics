"use client";

import "@/lib/patch-user-timing";
import { usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";
import { PaperInkController } from "./PaperInkController";

const INK_LAYER_CLASS = "pointer-events-none absolute inset-0 -z-10 size-full print:hidden";

export function PaperInk({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const pageRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const paintRef = useRef<HTMLCanvasElement>(null);
  const liveRef = useRef<HTMLCanvasElement>(null);
  const controllerRef = useRef<PaperInkController | null>(null);

  useEffect(() => {
    const [page, content, paint, live] = [pageRef.current, contentRef.current, paintRef.current, liveRef.current];
    if (!page || !content || !paint || !live) return;
    const controller = new PaperInkController({ page, content, paint, live });
    controllerRef.current = controller;
    return () => controller.dispose();
  }, []);

  useEffect(() => {
    controllerRef.current?.pageChanged();
  }, [pathname]);

  return (
    <div ref={pageRef} className="relative isolate min-h-dvh">
      <canvas ref={paintRef} aria-hidden className={INK_LAYER_CLASS} />
      <canvas ref={liveRef} aria-hidden className={INK_LAYER_CLASS} />
      <div ref={contentRef}>{children}</div>
      <div aria-hidden className="paper-grain pointer-events-none fixed inset-0 z-50 opacity-10 print:hidden" />
    </div>
  );
}
