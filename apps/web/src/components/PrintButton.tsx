"use client";

import { FilePdf, Printer } from "@phosphor-icons/react";
import { Button } from "./ui/Button";

type PrintPurpose = "print" | "pdf";

const PURPOSE = {
  print: { label: "Print this report", Icon: Printer },
  pdf: { label: "Save as PDF", Icon: FilePdf },
} as const;

/** Opens the browser's print dialog, which also saves the page as a PDF. */
export function PrintButton({ purpose = "print" }: { purpose?: PrintPurpose }) {
  const { label, Icon } = PURPOSE[purpose];
  return (
    <Button variant="primary" onClick={() => window.print()} className="shrink-0 whitespace-nowrap">
      <Icon size={18} weight="bold" aria-hidden />
      {label}
    </Button>
  );
}
