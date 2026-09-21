export type CapturedSplatAsset = {
  url: string;
  /** Row-major rigid transform: asset Z-up metres into scene Z-up metres. */
  transform: number[];
};

export type CapturedSplats = {
  revision: number;
  graph_hash: string;
  assets: CapturedSplatAsset[];
  display_only: true;
};
