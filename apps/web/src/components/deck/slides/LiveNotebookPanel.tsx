"use client";

import { LockSimple } from "@phosphor-icons/react";
import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { NOTEBOOK_URL } from "@/components/team/notebookUrl";
import { fadeReveal } from "../primitives";

type Access = "checking" | "open" | "locked";

/** The notebook's files are team-only, so the frame waits to learn whether this browser is signed in as the team. */
function useNotebookAccess(): Access {
  const [access, setAccess] = useState<Access>("checking");
  useEffect(() => {
    let current = true;
    fetch(NOTEBOOK_URL, { cache: "no-store" })
      .then((response) => current && setAccess(response.ok ? "open" : "locked"))
      .catch(() => current && setAccess("locked"));
    return () => {
      current = false;
    };
  }, []);
  return access;
}

function Locked() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-deck-hairline locked-hatch p-deck-gap text-center">
      <LockSimple aria-hidden weight="bold" className="size-deck-bar text-ink-muted" />
      <p className="font-display text-caption font-bold">Sign in as a team account to run the notebook here.</p>
    </div>
  );
}

export function LiveNotebookPanel() {
  const access = useNotebookAccess();
  return (
    <motion.div initial="enter" animate="present" exit="exit" variants={fadeReveal(0.1, 20)} className="h-deck-art bg-paper-raised shadow-xl">
      {access === "open" && <iframe src={NOTEBOOK_URL} title="Fine-tuning notebook, running in marimo" className="size-full" />}
      {access === "locked" && <Locked />}
    </motion.div>
  );
}
