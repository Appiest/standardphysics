import type { Metadata } from "next";
import { DeckLoader } from "@/components/deck/DeckLoader";
import { requireDemoRoutes } from "@/lib/demo-routes";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Standard Physics pitch",
};

export default function PresentPage() {
  requireDemoRoutes();
  return <DeckLoader />;
}
