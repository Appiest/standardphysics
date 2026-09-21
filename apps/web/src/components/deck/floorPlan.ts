import { backBar, cardReaderSpots, counterLayout } from "@/components/shop/counterLayout";
import { facts } from "@/lib/facts";
import { doors, furniture, walls, type ShopBox } from "@/lib/fixtureShop";
import { metersToInches } from "@/lib/units";

export const PLAN_UNITS_PER_METER = 100;

const west = Math.min(...walls.map((wall) => wall.position.x - wall.size.x / 2));
const east = Math.max(...walls.map((wall) => wall.position.x + wall.size.x / 2));
const north = Math.min(...walls.map((wall) => wall.position.z - wall.size.z / 2));
const south = Math.max(...walls.map((wall) => wall.position.z + wall.size.z / 2));
const wallThickness = walls[0].size.z < walls[0].size.x ? walls[0].size.z : walls[0].size.x;

export function toPlanX(x: number) {
  return (x - west) * PLAN_UNITS_PER_METER;
}

export function toPlanY(z: number) {
  return (z - north) * PLAN_UNITS_PER_METER;
}

export type PlanRect = { key: string; x: number; y: number; width: number; height: number };

export function rectAttributes({ x, y, width, height }: PlanRect) {
  return { x, y, width, height };
}

function spanRect(key: string, westX: number, eastX: number, northZ: number, southZ: number): PlanRect {
  return {
    key,
    x: toPlanX(westX),
    y: toPlanY(northZ),
    width: (eastX - westX) * PLAN_UNITS_PER_METER,
    height: (southZ - northZ) * PLAN_UNITS_PER_METER,
  };
}

function boxRect(box: ShopBox, key: string): PlanRect {
  const { position, size } = box;
  return spanRect(key, position.x - size.x / 2, position.x + size.x / 2, position.z - size.z / 2, position.z + size.z / 2);
}

export const planSize = {
  width: (east - west) * PLAN_UNITS_PER_METER,
  height: (south - north) * PLAN_UNITS_PER_METER,
  wall: wallThickness * PLAN_UNITS_PER_METER,
};

const frontDoor = doors[0];
const doorWestX = toPlanX(frontDoor.position.x - frontDoor.size.x / 2);
const doorEastX = toPlanX(frontDoor.position.x + frontDoor.size.x / 2);

export const planDoor = { westX: doorWestX, eastX: doorEastX, y: planSize.height - planSize.wall / 2 };

export function wallOutline(outline: { west: number; east: number; north: number; south: number }, doorway: { west: number; east: number }) {
  const { west: w, east: e, north: n, south: s } = outline;
  return `M ${doorway.east} ${s} L ${e} ${s} L ${e} ${n} L ${w} ${n} L ${w} ${s} L ${doorway.west} ${s}`;
}

const inset = planSize.wall / 2;
export const shopOutline = { west: inset, east: planSize.width - inset, north: inset, south: planSize.height - inset };
export const shopDoorway = { west: doorWestX, east: doorEastX };

const counterNorthZ = counterLayout.centerZ - counterLayout.depth / 2;
const counterSouthZ = counterLayout.centerZ + counterLayout.depth / 2;

export const planCounter = {
  low: spanRect("counter-low", counterLayout.low.westX, counterLayout.low.eastX, counterNorthZ, counterSouthZ),
  high: spanRect("counter-high", counterLayout.high.westX, counterLayout.high.eastX, counterNorthZ, counterSouthZ),
  backBar: spanRect("back-bar", backBar.westX, backBar.eastX, backBar.centerZ - backBar.depth / 2, backBar.centerZ + backBar.depth / 2),
  reader: { x: toPlanX(cardReaderSpots.onLowCounter.x), y: toPlanY(cardReaderSpots.onLowCounter.z) },
  frontY: toPlanY(counterSouthZ),
};

const seating = furniture.filter((box) => box.label === "Table" || box.label === "Chair");
const displayCases = furniture
  .filter((box) => box.label === "Display case")
  .sort((left, right) => left.position.x - right.position.x);

export const planSeating = seating.map((box, index) => ({ ...boxRect(box, `${box.label}-${index}`), round: box.label === "Table" }));
export const planCases = displayCases.map((box, index) => boxRect(box, `case-${index}`));

const westCase = displayCases[0];
const eastCase = displayCases[displayCases.length - 1];
const caseGapWestX = westCase.position.x + westCase.size.x / 2;
const caseGapEastX = eastCase.position.x - eastCase.size.x / 2;

export const planCaseGap = {
  westX: toPlanX(caseGapWestX),
  eastX: toPlanX(caseGapEastX),
  y: toPlanY(westCase.position.z),
  inches: metersToInches(caseGapEastX - caseGapWestX),
};

export const measurements = {
  doorInches: metersToInches(frontDoor.size.x),
  caseGapInches: planCaseGap.inches,
  counterInches: facts.counterHeightInLawsuit.value,
};

export function formatInches(inches: number) {
  const rounded = Math.round(inches * 10) / 10;
  return `${Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1)} in`;
}

export function planPerimeterPoints(rect: PlanRect, spacing: number) {
  const perimeter = 2 * (rect.width + rect.height);
  const count = Math.max(4, Math.round(perimeter / spacing));
  return Array.from({ length: count }, (_, index) => pointOnPerimeter(rect, (index / count) * perimeter));
}

function pointOnPerimeter(rect: PlanRect, distance: number) {
  const { x, y, width, height } = rect;
  if (distance < width) return { x: x + distance, y };
  if (distance < width + height) return { x: x + width, y: y + distance - width };
  if (distance < 2 * width + height) return { x: x + width - (distance - width - height), y: y + height };
  return { x, y: y + height - (distance - 2 * width - height) };
}
