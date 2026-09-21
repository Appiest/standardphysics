import type { Metadata } from "next";
import type { ReactNode } from "react";
import { appFontVariables } from "../fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: "Standard Physics drafting sheet",
  description: "Drag across the sheet and a pen drafts an accessible route with real ADA dimensions.",
};

export default function BlueprintLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={appFontVariables}>
      <body className="min-h-dvh">{children}</body>
    </html>
  );
}
