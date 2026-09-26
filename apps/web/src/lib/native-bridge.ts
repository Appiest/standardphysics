/**
 * Talking to the iPhone app when the owner view runs inside it.
 *
 * The app loads the web in a web view and adds a message handler named
 * `standardPhysics`. Messages carry ids, never tokens (docs/UX.md, the bridge).
 * The app marks its web view by adding `StandardPhysicsApp/<build>` to the user
 * agent, so the server can lay the page out for it before any script runs.
 */

export type AppMessage =
  | { type: "takePhoto"; requestId: string }
  | { type: "addRoom"; scanId: string }
  | { type: "saveReport" }
  | { type: "share"; url: string; title: string }
  | { type: "openLink"; url: string }
  | { type: "stageChanged"; scanId: string; stage: string };

type Handler = { postMessage: (body: unknown) => void };
type WithWebkit = { webkit?: { messageHandlers?: Record<string, Handler | undefined> } };

export const APP_USER_AGENT = "StandardPhysicsApp/";

export function isAppUserAgent(userAgent: string | null): boolean {
  return userAgent?.includes(APP_USER_AGENT) ?? false;
}

function handler(name: string): Handler | undefined {
  if (typeof window === "undefined") return undefined;
  return (window as unknown as WithWebkit).webkit?.messageHandlers?.[name];
}

export function inApp(): boolean {
  return handler("standardPhysics") !== undefined;
}

/** Sends a message to the app. False when the page isn't running inside it. */
export function tellApp(message: AppMessage): boolean {
  const app = handler("standardPhysics");
  if (!app) return false;
  app.postMessage(message);
  return true;
}

/** The older app's one message, which opens the walk. */
export function startWalk(): boolean {
  const app = handler("nativeCapture");
  if (!app) return false;
  app.postMessage("scanShop");
  return true;
}

export type AppCallbacks = { photoSent: (requestId: string) => void };

/** Lets the app call back into the page, for example once a photo it took has uploaded. */
export function listenToApp(callbacks: AppCallbacks): () => void {
  const target = window as unknown as { standardPhysics?: AppCallbacks };
  target.standardPhysics = callbacks;
  return () => {
    if (target.standardPhysics === callbacks) delete target.standardPhysics;
  };
}
