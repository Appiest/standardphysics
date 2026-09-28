import { useCurrentFrame } from "remotion";
import { facts } from "../../../../web/src/lib/facts";
import { FinePrint } from "../../components/FinePrint";
import { GlowDot } from "../../components/Glow";
import { ScanBar } from "../../components/ScanBar";
import { RevealLines } from "../../components/Type";
import { between, drawn, progress, sweep } from "../../lib/ease";
import { REEL } from "../../lib/timing";
import { LawsuitField, lawsuitFieldCenter } from "./LawsuitField";
import { measureDotY } from "./MeasureScene";

const lawsuits = facts.adaLawsuitsFiled2025.value;
const counted = new Intl.NumberFormat("en-US");

export const FIELD = { fillStart: 6, fillEnd: 78, collapseAt: 104, barAt: 122, end: 140 } as const;

function shownAt(frame: number) {
  const fill = progress(frame, FIELD.fillStart, FIELD.fillEnd - FIELD.fillStart, (t) => t * t * (3 - 2 * t));
  return 1 + fill * (lawsuits - 1);
}

export function FieldScene() {
  const frame = useCurrentFrame();
  const shown = shownAt(frame);
  const collapse = progress(frame, FIELD.collapseAt, FIELD.barAt - FIELD.collapseAt + 4, sweep);
  const copyGone = progress(frame, FIELD.collapseAt - 4, 12, sweep);
  const bar = between(frame, [FIELD.barAt - 6, FIELD.barAt + 2], [0, 1]);
  const fieldFade = between(frame, [FIELD.barAt - 2, FIELD.barAt + 6], [1, 0]);
  const rotation = frame * 0.004 + progress(frame, 0, FIELD.fillEnd, drawn) * 1.2;
  return (
    <div className="absolute inset-0">
      <LawsuitField count={lawsuits} state={{ shown, rotation, collapse, opacity: fieldFade }} centerY={measureDotY} />
      <GlowDot x={lawsuitFieldCenter.x} y={measureDotY} size={34 * (1 - collapse)} />
      <div className="absolute inset-x-safe-side top-safe-top" style={{ opacity: 1 - copyGone, transform: `translateY(${-copyGone * 60}px)` }}>
        <p className="reel-copy figures text-poster">{counted.format(Math.round(shown))}</p>
        <RevealLines lines={["ADA lawsuits were filed", "against businesses in 2025."]} at={FIELD.fillStart + 10} className="reel-caption mt-6 block text-caption" />
      </div>
      <div style={{ opacity: 1 - copyGone }}>
        <FinePrint at={FIELD.fillEnd} className="absolute inset-x-safe-side bottom-safe-bottom">
          {facts.adaLawsuitsFiled2025.source}
        </FinePrint>
      </div>
      {bar > 0 && <ScanBar at={measureDotY / REEL.height} intensity={bar} trail={0} />}
    </div>
  );
}
