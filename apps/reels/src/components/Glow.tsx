type GlowDotProps = { x: number; y: number; size: number; opacity?: number };

/** A point of tape-yellow light: a hot white core falling off through yellow into a wide soft halo. */
export function GlowDot({ x, y, size, opacity = 1 }: GlowDotProps) {
  return (
    <div
      aria-hidden
      className="absolute rounded-full"
      style={{
        left: x,
        top: y,
        width: size,
        height: size,
        opacity,
        translate: "-50% -50%",
        background: "radial-gradient(circle at 50% 50%, #fffdf2 0 18%, #f6be1a 46%, #d99a00 70%, transparent 71%)",
        boxShadow: `0 0 ${size * 0.8}px ${size * 0.25}px rgba(246,190,26,0.65), 0 0 ${size * 3}px ${size}px rgba(246,190,26,0.25)`,
      }}
    />
  );
}
