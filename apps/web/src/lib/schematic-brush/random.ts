export function random(min = 0, max = 1) {
  return min + Math.random() * (max - min);
}

export function randomInt(min: number, maxExclusive: number) {
  return Math.floor(random(min, maxExclusive));
}

export function chance(probability: number) {
  return Math.random() < probability;
}

export function randomSign() {
  return chance(0.5) ? -1 : 1;
}

export function pick<T>(items: readonly T[]): T {
  return items[randomInt(0, items.length)];
}

export function weightedPick<T>(weights: readonly (readonly [T, number])[]): T {
  const total = weights.reduce((sum, [, weight]) => sum + weight, 0);
  let roll = random(0, total);
  for (const [item, weight] of weights) {
    roll -= weight;
    if (roll < 0) return item;
  }
  return weights[weights.length - 1][0];
}

export function shuffled<T>(items: readonly T[]): T[] {
  const result = [...items];
  for (let i = result.length - 1; i > 0; i--) {
    const j = randomInt(0, i + 1);
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}

function hash(index: number) {
  const value = Math.sin(index * 127.1 + 311.7) * 43758.5453;
  return value - Math.floor(value);
}

export function smoothNoise(x: number) {
  const index = Math.floor(x);
  const fraction = x - index;
  const eased = fraction * fraction * (3 - 2 * fraction);
  return hash(index) + (hash(index + 1) - hash(index)) * eased;
}
