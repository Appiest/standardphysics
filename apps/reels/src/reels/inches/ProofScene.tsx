import { Img, staticFile, useCurrentFrame } from "remotion";
import { TapeLabel } from "../../components/Type";
import { drawn, progress, snap, sweep } from "../../lib/ease";

const DOOR_SHOT = { width: 1324, height: 820, label: { x: 661, y: 357 } } as const;
const CARD_WIDTH = 940;
const CARD_SCALE = CARD_WIDTH / DOOR_SHOT.width;

export const PROOF = { flyIn: 26, zoomAt: 34, findingAt: 58, end: 120 } as const;

function WindowCard({ frame }: { frame: number }) {
  const arrive = progress(frame, 0, PROOF.flyIn, drawn);
  const zoom = progress(frame, PROOF.zoomAt, 40, sweep);
  const scale = 1 + zoom * 1.4;
  const origin = `${(DOOR_SHOT.label.x / DOOR_SHOT.width) * 100}% ${(DOOR_SHOT.label.y / DOOR_SHOT.height) * 100}%`;
  return (
    <div className="absolute left-1/2 top-[520px]" style={{ perspective: 1600, translate: "-50% 0" }}>
      <div
        className="overflow-hidden rounded-[28px] bg-paper-raised shadow-[0_40px_120px_rgba(0,0,0,0.6),0_0_0_1px_rgba(255,255,255,0.08)]"
        style={{
          width: CARD_WIDTH,
          height: DOOR_SHOT.height * CARD_SCALE,
          opacity: Math.min(1, arrive * 2),
          transform: `translateY(${(1 - arrive) * 700}px) rotateX(${(1 - arrive) * 38}deg) rotateZ(${(1 - arrive) * -8}deg)`,
        }}
      >
        <Img src={staticFile("stills/app-door.png")} className="size-full object-cover" style={{ transform: `scale(${scale})`, transformOrigin: origin }} />
      </div>
    </div>
  );
}

function FindingCard({ frame }: { frame: number }) {
  const rise = progress(frame, PROOF.findingAt, 18, snap);
  return (
    <div
      className="absolute left-1/2 top-[1150px] w-[860px] overflow-hidden rounded-[24px] bg-paper-raised shadow-[0_30px_90px_rgba(0,0,0,0.55),0_0_60px_rgba(246,190,26,0.35)]"
      style={{ opacity: rise, transform: `translate(-50%, ${(1 - rise) * 260}px) rotate(${(1 - rise) * 4}deg)` }}
    >
      <Img src={staticFile("stills/app-finding.png")} className="block w-full" />
    </div>
  );
}

export function ProofScene() {
  const frame = useCurrentFrame();
  return (
    <div className="absolute inset-0">
      <WindowCard frame={frame} />
      <FindingCard frame={frame} />
      <div className="absolute inset-x-safe-side top-safe-top">
        <TapeLabel at={10} className="reel-caption text-caption">
          Then it tells you what’s off.
        </TapeLabel>
      </div>
    </div>
  );
}
