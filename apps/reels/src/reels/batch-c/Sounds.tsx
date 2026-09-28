import { Audio, Sequence, staticFile } from "remotion";

export type OneShot = { at: number; file: string; volume?: number };

export const shot = (at: number, file: string, volume = 1): OneShot => ({ at, file, volume });

/** One-shot effects by file name, for sounds outside the shared kit (batch C's c-*.wav). */
export function OneShots({ shots }: { shots: readonly OneShot[] }) {
  return (
    <>
      {shots.map((item, index) => (
        <Sequence key={index} from={item.at} layout="none">
          <Audio src={staticFile(`sound/${item.file}.wav`)} volume={item.volume} />
        </Sequence>
      ))}
    </>
  );
}
