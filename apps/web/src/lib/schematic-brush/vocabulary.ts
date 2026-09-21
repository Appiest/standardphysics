import { pick, randomInt } from "./random";

export const INCHES_PER_UNIT = 6;
export const SHEET_SCALE_LABEL = `1" = 4'-0"`;

const REQUIREMENTS = ['36" min', '32" min', '48" max', '36" max', '80" min', '30" × 48"', "1:12 max", '18" min'];

const CODE_SECTIONS = ["§403.5.1", "§404.2.3", "§304.3", "§305.3", "§308.2.1", "§405.2", "§904.4.1", "§307.4", "§404.2.4.1"];

export function inchesForLength(lengthPx: number, unit: number) {
  return `${Math.round((lengthPx / unit) * INCHES_PER_UNIT)}"`;
}

export function requirementNote() {
  return pick(REQUIREMENTS);
}

export function codeSection() {
  return pick(CODE_SECTIONS);
}

export function detailNumber() {
  return String(randomInt(1, 13));
}

export function sheetReference() {
  return `A${randomInt(1, 5)}.${randomInt(1, 5)}`;
}
