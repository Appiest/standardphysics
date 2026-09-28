import { CircleNotch } from "@phosphor-icons/react/dist/ssr";

export function ActivitySpinner({ className = "", size = 20 }: { className?: string; size?: number }) {
  return (
    <span className={`inline-flex items-center text-ink-muted ${className}`} role="status" aria-label="Working">
      <CircleNotch size={size} className="motion-safe:animate-spin" aria-hidden />
    </span>
  );
}
