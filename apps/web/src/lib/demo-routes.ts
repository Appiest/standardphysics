import { notFound } from "next/navigation";

/** The pitch deck and the drafting sandbox are not part of the product.
 *
 * They are reachable by URL in any build that serves them, so a deployment
 * hides them unless someone asks for them. Set SP_SHOW_DEMO_ROUTES=1 before a
 * demo, and the shops a customer signs in to never sit next to a sales deck. */
export function requireDemoRoutes(): void {
  if (process.env.SP_SHOW_DEMO_ROUTES !== "1") notFound();
}
