import { Audio, interpolate, Sequence, staticFile, useVideoConfig } from "remotion";

/** The meme one-shots from scripts/sound_memes.py. */
type MemeSound = "m-vine-boom" | "m-trombone" | "m-buzzer" | "m-record-stop" | "m-gasp" | "m-laugh" | "m-cheer" | "m-splat" | "m-clown-horn" | "m-boing";

export type Sound = "boom" | "hit" | "whoosh" | "whoosh-down" | "riser" | "tick" | "tape" | "shutter" | "scan" | "scratch" | "pop" | MemeSound;

export type Cue = { at: number; sound: Sound; volume?: number };

export const cue = (at: number, sound: Sound, volume = 1): Cue => ({ at, sound, volume });

export function repeatCue(from: number, to: number, every: number, sound: Sound, volume = 1): Cue[] {
  return Array.from({ length: Math.floor((to - from) / every) + 1 }, (_, index) => cue(from + index * every, sound, volume));
}

function Bed({ track, volume, loops }: { track: string; volume: number; loops: boolean }) {
  const { durationInFrames } = useVideoConfig();
  const level = (frame: number) =>
    loops ? volume : interpolate(frame, [0, 6, durationInFrames - 24, durationInFrames], [0, volume, volume, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return <Audio src={staticFile(`sound/${track}.wav`)} volume={level} />;
}

/** The reel's sound: a music bed under everything, and one-shot effects landing on the frames the motion hits. */
type SoundtrackProps = { bed: string; bedVolume?: number; cues: readonly Cue[]; loops?: boolean };

export function Soundtrack({ bed, bedVolume = 0.35, cues, loops = false }: SoundtrackProps) {
  return (
    <>
      <Bed track={bed} volume={bedVolume} loops={loops} />
      {cues.map((item, index) => (
        <Sequence key={index} from={item.at} layout="none">
          <Audio src={staticFile(`sound/${item.sound}.wav`)} volume={item.volume} />
        </Sequence>
      ))}
    </>
  );
}
