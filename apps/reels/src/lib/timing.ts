export const FPS = 30;
export const REEL = { width: 1080, height: 1920 } as const;

export const seconds = (value: number) => Math.round(value * FPS);
export const toMs = (frame: number) => (frame / FPS) * 1000;
