import { DraftingSheet } from "@/components/blueprint/DraftingSheet";
import { requireDemoRoutes } from "@/lib/demo-routes";

export const dynamic = "force-dynamic";

export default function BrushPage() {
  requireDemoRoutes();
  return <DraftingSheet />;
}
