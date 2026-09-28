import { LogoMark } from "../../components/Logo";

export type SheetFact = { label: string; value: string };

/** The drafting sheet's title block: who drew it, and the facts of the walk that produced it. */
export function TitleBlock({ facts }: { facts: SheetFact[] }) {
  return (
    <div className="absolute inset-x-safe-side border-[3px] border-ink bg-paper-raised/60" style={{ top: 1330 }}>
      <div className="flex items-center gap-5 border-b-[3px] border-ink px-6 py-4">
        <LogoMark at={-30} size={72} />
        <p className="reel-copy text-label">Standard Physics</p>
        <p className="ml-auto font-body text-fineprint text-ink-muted">standardphysics.app</p>
      </div>
      <div className="flex">
        {facts.map((fact) => (
          <div key={fact.label} className="flex flex-1 flex-col justify-center border-r-[3px] border-ink px-6 py-3 last:border-r-0">
            <p className="font-body text-fineprint text-ink-muted">{fact.label}</p>
            <p className="reel-copy figures whitespace-nowrap text-label">{fact.value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
