import type { Blocked } from "@/types/contracts";

function lower(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

function pair(detail: string): [string, string] {
  const [moved, other = "something"] = detail.split(" into ");
  return [moved, lower(other.replace(/^the /i, ""))];
}

const SENTENCE: Record<string, (detail: string) => string> = {
  collided: (detail) => {
    const [moved, other] = pair(detail);
    return `The ${lower(moved)} overlaps the ${other}.`;
  },
  blocked_a_door: (detail) => {
    const [moved, door] = pair(detail);
    return `The ${lower(moved)} is in the way of the ${door}.`;
  },
  moved_something_fixed: (detail) => `The ${lower(detail)} stays where it is.`,
  left_the_floor: (detail) => `The ${lower(detail)} would go past the edge of the room.`,
};

export function blockedSentence(blocked: Blocked): string {
  const write = SENTENCE[blocked.reason];
  return write ? write(blocked.detail) : "That spot doesn't work for this piece.";
}
