import { AI_REPLACE_LENGTH, AiReplaceUs } from "./batch-a/AiReplaceUs";
import { DOLL_LENGTH, DollMeme } from "./batch-a/DollMeme";
import { FLOP_LENGTH, FlopCore } from "./batch-a/FlopCore";
import { LIBRARY_POV_LENGTH, LibraryPov } from "./batch-a/LibraryPov";
import { THERAPY_LENGTH, TherapyMeme } from "./batch-a/TherapyMeme";
import type { ReelSpec } from "./registry";

export const batchA: ReelSpec[] = [
  { id: "LibraryPov", component: LibraryPov, durationInFrames: LIBRARY_POV_LENGTH },
  { id: "TherapyMeme", component: TherapyMeme, durationInFrames: THERAPY_LENGTH },
  { id: "DollMeme", component: DollMeme, durationInFrames: DOLL_LENGTH },
  { id: "FlopCore", component: FlopCore, durationInFrames: FLOP_LENGTH },
  { id: "AiReplaceUs", component: AiReplaceUs, durationInFrames: AI_REPLACE_LENGTH },
];
