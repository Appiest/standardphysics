"use client";

import { SignOut } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/Button";

export function SignOutButton() {
  const router = useRouter();
  const [leaving, setLeaving] = useState(false);

  async function signOut() {
    setLeaving(true);
    await fetch("/api/auth/sign-out", { method: "POST" });
    router.replace("/sign-in");
    router.refresh();
  }

  return (
    <Button squared onClick={signOut} disabled={leaving}>
      <SignOut size={18} aria-hidden />
      {leaving ? "Signing out" : "Sign out"}
    </Button>
  );
}
