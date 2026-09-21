type Orientation = "horizontal" | "vertical";

const ORIENTATION_CLASS: Record<Orientation, { band: string; cell: string }> = {
  horizontal: { band: "flex-row", cell: "border-l first:border-l-0" },
  vertical: { band: "flex-col", cell: "border-t first:border-t-0" },
};

export function ZoneBand({ labels, orientation, className = "" }: { labels: string[]; orientation: Orientation; className?: string }) {
  const classes = ORIENTATION_CLASS[orientation];
  return (
    <div aria-hidden className={`flex ${classes.band} ${className}`}>
      {labels.map((label) => (
        <span
          key={label}
          className={`flex flex-1 items-center justify-center border-ink/40 text-xs font-medium text-ink-muted tabular-nums ${classes.cell}`}
        >
          {label}
        </span>
      ))}
    </div>
  );
}
