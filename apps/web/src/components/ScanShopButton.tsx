"use client";

import { Scan } from "@phosphor-icons/react";
import { useState } from "react";
import { Button } from "./ui/Button";

type NativeBridge = { webkit?: { messageHandlers?: { nativeCapture?: { postMessage: (body: string) => void } } } };

export function ScanShopButton() {
  const [needsApp, setNeedsApp] = useState(false);

  function startScan() {
    const bridge = (window as unknown as NativeBridge).webkit?.messageHandlers?.nativeCapture;
    if (bridge) {
      bridge.postMessage("scanShop");
      return;
    }
    setNeedsApp(true);
  }

  return (
    <div className="flex flex-col items-start gap-3">
      <Button variant="primary" squared onClick={startScan}>
        <Scan size={20} weight="bold" aria-hidden />
        Scan your shop
      </Button>
      {needsApp && (
        <p className="text-sm text-ink-muted" role="status">
          Open Standard Physics on a LiDAR iPhone to walk your shop.
        </p>
      )}
    </div>
  );
}
