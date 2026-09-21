import type { Box } from "@/lib/schematic-brush/geometry";

const SOLID_SELECTOR = "img, svg, canvas, video, iframe, input, select, textarea, button, a, [role=button], [data-ink-avoid]";
const TRANSPARENT_BACKGROUNDS = new Set(["rgba(0, 0, 0, 0)", "transparent"]);
const BORDER_SIDES = ["Top", "Right", "Bottom", "Left"] as const;
const LARGEST_SURFACE_SHARE = 0.6;

function textRects(content: HTMLElement): DOMRect[] {
  const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (node.textContent?.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
  });
  const range = document.createRange();
  const rects: DOMRect[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    range.selectNodeContents(node);
    rects.push(...Array.from(range.getClientRects()));
  }
  return rects;
}

function hasVisibleBorder(style: CSSStyleDeclaration) {
  return BORDER_SIDES.some((side) => parseFloat(style[`border${side}Width`]) > 0 && style[`border${side}Style`] !== "none");
}

function paintsSurface(style: CSSStyleDeclaration) {
  const filled = !TRANSPARENT_BACKGROUNDS.has(style.backgroundColor) || style.backgroundImage !== "none";
  return filled || style.boxShadow !== "none" || hasVisibleBorder(style);
}

function scrollsContent(element: Element, style: CSSStyleDeclaration) {
  const scrollable = /(auto|scroll)/.test(`${style.overflowX} ${style.overflowY}`);
  return scrollable && (element.scrollHeight > element.clientHeight + 1 || element.scrollWidth > element.clientWidth + 1);
}

function isSolid(element: Element, pageArea: number) {
  if (element.matches(SOLID_SELECTOR)) return true;
  const style = getComputedStyle(element);
  if (scrollsContent(element, style)) return true;
  const rect = element.getBoundingClientRect();
  return paintsSurface(style) && rect.width * rect.height < pageArea * LARGEST_SURFACE_SHARE;
}

export function collectKeepOuts(content: HTMLElement, page: HTMLElement, clearancePx: number): Box[] {
  const origin = page.getBoundingClientRect();
  const pageArea = origin.width * origin.height;
  const solids = Array.from(content.querySelectorAll("*"))
    .filter((element) => isSolid(element, pageArea))
    .map((element) => element.getBoundingClientRect());
  return [...textRects(content), ...solids]
    .filter((rect) => rect.width > 0 && rect.height > 0)
    .map((rect) => [
      rect.left - origin.left - clearancePx,
      rect.top - origin.top - clearancePx,
      rect.right - origin.left + clearancePx,
      rect.bottom - origin.top + clearancePx,
    ]);
}
