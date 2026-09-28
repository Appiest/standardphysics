export function seededRandom(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The schematic brush draws with Math.random, so every render tab has to see the same sequence to draw the same sheet. */
export function withSeed<T>(seed: number, build: () => T): T {
  const original = Math.random;
  Math.random = seededRandom(seed);
  try {
    return build();
  } finally {
    Math.random = original;
  }
}
