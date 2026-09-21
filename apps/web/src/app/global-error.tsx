"use client";

import { useEffect } from "react";

/** The last resort, when the workspace layout itself could not render.
 *
 * Next replaces the whole document here, so this file carries its own html and
 * body and cannot reach the app's fonts or theme. */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="en">
      <body style={{ background: "#f6f5f1", color: "#1b1c1e", fontFamily: "ui-sans-serif, system-ui, sans-serif" }}>
        <main style={{ margin: "0 auto", maxWidth: "34rem", padding: "6rem 1.5rem", display: "grid", gap: "1.25rem" }}>
          <h1 style={{ fontSize: "2rem", fontWeight: 600, margin: 0 }}>Standard Physics did not start</h1>
          <p style={{ margin: 0, color: "#5d5e61" }}>
            Your shop and its measurements are safe. Reloading usually clears this.
          </p>
          <button
            type="button"
            onClick={reset}
            style={{
              justifySelf: "start",
              background: "#1b1c1e",
              color: "#f6f5f1",
              border: 0,
              padding: "0.75rem 1.25rem",
              fontSize: "1rem",
              cursor: "pointer",
            }}
          >
            Reload
          </button>
        </main>
      </body>
    </html>
  );
}
