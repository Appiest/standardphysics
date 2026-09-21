const METERS_PER_INCH = 0.0254;

export function inchesToMeters(inches: number): number {
  return inches * METERS_PER_INCH;
}

export function metersToInches(meters: number): number {
  return meters / METERS_PER_INCH;
}

export function formatFeetAndInches(meters: number): string {
  const totalInches = Math.round(metersToInches(meters));
  const feet = Math.floor(totalInches / 12);
  return `${feet}'-${totalInches - feet * 12}"`;
}
