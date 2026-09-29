import { notFound } from "next/navigation";

/** The drafting sandbox is not part of the product.
 *
 * It is reachable by URL in any build that serves it, so a deployment hides it
 * unless someone asks for it with SP_SHOW_DEMO_ROUTES=1. The pitch deck at
 * /present is public on purpose and is not behind this. */
export function requireDemoRoutes(): void {
  if (process.env.SP_SHOW_DEMO_ROUTES !== "1") notFound();
}
