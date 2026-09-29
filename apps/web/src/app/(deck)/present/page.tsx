import type { Metadata } from "next";
import { DeckLoader } from "@/components/deck/DeckLoader";

export const metadata: Metadata = {
  title: "Standard Physics pitch",
};

export default function PresentPage() {
  return <DeckLoader />;
}
