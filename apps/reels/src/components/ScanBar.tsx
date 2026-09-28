type ScanBarProps = {
  /** Where the bar sits, from 0 at the top (or left) of the frame to 1 at the bottom (or right). */
  at: number;
  axis?: "horizontal" | "vertical";
  intensity?: number;
  /** How far the lit wash trails behind the bar, as a share of the frame. */
  trail?: number;
  direction?: 1 | -1;
};

function trailGradient(axis: "horizontal" | "vertical", direction: 1 | -1) {
  const towards = axis === "horizontal" ? (direction === 1 ? "to top" : "to bottom") : direction === 1 ? "to left" : "to right";
  return `linear-gradient(${towards}, rgba(246,190,26,0.34), rgba(246,190,26,0.08) 40%, transparent)`;
}

export function ScanBar({ at, axis = "horizontal", intensity = 1, trail = 0.18, direction = 1 }: ScanBarProps) {
  const horizontal = axis === "horizontal";
  const position = `${at * 100}%`;
  const trailSize = `${trail * 100}%`;
  const trailOffset = direction === 1 ? `calc(${position} - ${trailSize})` : position;
  const bar = horizontal
    ? { top: position, left: "-5%", right: "-5%", height: 6, translate: "0 -50%" }
    : { left: position, top: "-5%", bottom: "-5%", width: 6, translate: "-50% 0" };
  const wash = horizontal ? { top: trailOffset, left: 0, right: 0, height: trailSize } : { left: trailOffset, top: 0, bottom: 0, width: trailSize };
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden" style={{ opacity: intensity }}>
      <div className="absolute mix-blend-screen" style={{ ...wash, background: trailGradient(axis, direction) }} />
      <div
        className="absolute bg-tape-light"
        style={{ ...bar, boxShadow: "0 0 10px 3px #f6be1a, 0 0 42px 14px rgba(246,190,26,0.75), 0 0 140px 40px rgba(246,190,26,0.35)" }}
      />
    </div>
  );
}
