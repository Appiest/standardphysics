import { MaskedLines } from "../primitives";
import { ScanWipe } from "../ScanWipe";

function NameAndTagline({ tagline }: { tagline: string }) {
  return (
    <div className="deck-gutter flex h-full flex-col justify-between">
      <h1 className="font-display text-display font-extrabold">
        <ScanWipe delay={0.3} duration={1.8}>
          Standard
        </ScanWipe>
        <ScanWipe delay={0.55} duration={1.8}>
          Physics
        </ScanWipe>
      </h1>
      <p className="font-display text-lede font-bold text-ink-muted">
        <MaskedLines lines={[tagline]} delay={1.9} />
      </p>
    </div>
  );
}

export function TitleSlide() {
  return <NameAndTagline tagline="Making accessibility more accessible." />;
}

