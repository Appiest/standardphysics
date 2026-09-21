import { buildFreeSpace, planAmbientRoutes } from "@/lib/schematic-brush/ambient";
import { SchematicBrush } from "@/lib/schematic-brush/brush";
import { polylineLength, type Box } from "@/lib/schematic-brush/geometry";
import { DRAFTING_UNIT_PX } from "@/lib/schematic-brush/sheet";
import { clearCanvas, createTextMeasurer, fitCanvases, readPalette, startInkLoop } from "./inkCanvas";
import { collectKeepOuts } from "./keepOuts";

const PEN_SPEED_MULTIPLIER = 2.2;
const HAND_SPEED_PX_PER_MS = 0.8;
const ROUTE_OVERLAP = 0.6;
const CLEARANCE_PX = 20;
const RESCAN_DELAY_MS = 350;

export interface PaperInkElements {
  page: HTMLElement;
  content: HTMLElement;
  paint: HTMLCanvasElement;
  live: HTMLCanvasElement;
}

export class PaperInkController {
  private readonly brush: SchematicBrush;
  private readonly stopLoop: () => void;
  private readonly resizeObserver: ResizeObserver;
  private readonly mutationObserver: MutationObserver;
  private rescanTimer: number | undefined;
  private needsRedraft = true;
  private fontsReady = false;

  constructor(private readonly elements: PaperInkElements) {
    const palette = readPalette(elements.page, "--color-paper");
    this.brush = new SchematicBrush({
      unit: DRAFTING_UNIT_PX,
      measureText: createTextMeasurer(palette.fontFamily),
      now: () => performance.now(),
      speed: PEN_SPEED_MULTIPLIER,
    });
    this.stopLoop = startInkLoop(this.brush, elements.paint, elements.live, palette);
    this.resizeObserver = new ResizeObserver(() => this.scheduleRescan());
    this.resizeObserver.observe(elements.page);
    this.mutationObserver = new MutationObserver(() => this.scheduleRescan());
    this.mutationObserver.observe(elements.content, { childList: true, subtree: true, characterData: true, attributeFilter: ["class", "hidden", "open"] });
    document.fonts.ready.then(() => {
      this.fontsReady = true;
      this.scheduleRescan();
    });
  }

  pageChanged() {
    this.needsRedraft = true;
    this.scheduleRescan();
  }

  dispose() {
    window.clearTimeout(this.rescanTimer);
    this.stopLoop();
    this.resizeObserver.disconnect();
    this.mutationObserver.disconnect();
    this.brush.clear();
  }

  private scheduleRescan() {
    window.clearTimeout(this.rescanTimer);
    this.rescanTimer = window.setTimeout(() => this.rescan(), RESCAN_DELAY_MS);
  }

  private rescan() {
    if (!this.fontsReady) return;
    const { page, content, paint, live } = this.elements;
    fitCanvases(page, paint, live);
    const keepOuts = collectKeepOuts(content, page, CLEARANCE_PX);
    const bounds: Box = [0, 0, page.clientWidth, page.clientHeight];
    if (this.needsRedraft || this.brush.inkTouches(keepOuts)) {
      this.redraft(keepOuts, bounds);
      return;
    }
    this.brush.setKeepOuts(keepOuts, bounds);
  }

  private redraft(keepOuts: Box[], bounds: Box) {
    const { page, paint, live } = this.elements;
    this.needsRedraft = false;
    this.brush.clear();
    clearCanvas(paint);
    clearCanvas(live);
    this.brush.setKeepOuts(keepOuts, bounds);
    let startDelay = 0;
    for (const route of planAmbientRoutes(buildFreeSpace(page.clientWidth, page.clientHeight, keepOuts))) {
      const duration = polylineLength(route) / HAND_SPEED_PX_PER_MS;
      this.brush.draftRoute(route, duration, startDelay);
      startDelay += duration * ROUTE_OVERLAP;
    }
  }
}
