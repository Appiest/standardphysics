import type { Metadata } from "next";
import type { ReactNode } from "react";
import { PaperInk } from "@/components/blueprint/PaperInk";
import { appFontVariables } from "../fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: "Standard Physics",
  description: "See what gets in the way of customers in your shop, and how to fix it.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={appFontVariables}>
      <body className="min-h-dvh">
        <PaperInk>{children}</PaperInk>
      </body>
    </html>
  );
}
