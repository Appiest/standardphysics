"use client";

import { motion } from "motion/react";
import { easeDrawn } from "@/lib/motion";

type AppCaptureProps = {
  aspect?: "wide" | "square";
  alt: string;
  image?: string;
  video?: string;
  address?: string;
  drift?: boolean;
};

const aspectClass = { wide: "aspect-[8/5]", square: "aspect-[21/20]" };

export function AppCapture({ alt, image, video, address = "standardphysics.app", drift = true, aspect = "wide" }: AppCaptureProps) {
  return (
    <motion.figure
      initial={{ opacity: 0, y: 30, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ duration: 0.7, ease: easeDrawn }}
      className="app-capture flex w-full flex-col overflow-hidden rounded-xl bg-paper-raised shadow-2xl"
    >
      <div aria-hidden className="flex items-center gap-deck-hairline bg-paper-sunken px-deck-hairline py-2">
        <span className="size-3 rounded-full bg-ink-faint" />
        <span className="size-3 rounded-full bg-ink-faint" />
        <span className="size-3 rounded-full bg-ink-faint" />
        <span className="ml-deck-hairline font-display text-sm font-bold text-ink-muted">{address}</span>
      </div>
      <div className={`relative overflow-hidden ${aspectClass[aspect]}`}>
        {video ? (
          <video src={video} aria-label={alt} autoPlay muted playsInline loop preload="auto" className="size-full object-cover object-left-top" />
        ) : (
          <motion.img
            src={image}
            alt={alt}
            initial={{ scale: 1 }}
            animate={drift ? { scale: 1.06 } : { scale: 1 }}
            transition={{ duration: 12, ease: "linear" }}
            className="size-full origin-center object-cover object-left-top"
          />
        )}
      </div>
    </motion.figure>
  );
}
