import { loadFont as loadKarla } from "@remotion/google-fonts/Karla";
import { loadFont as loadLibreFranklin } from "@remotion/google-fonts/LibreFranklin";
import { useEffect, useState } from "react";
import { continueRender, delayRender } from "remotion";

const libreFranklin = loadLibreFranklin("normal", { weights: ["500", "700", "800", "900"], subsets: ["latin"] });
const karla = loadKarla("normal", { weights: ["400", "500", "700"], subsets: ["latin"] });
const allFaces = Promise.all([libreFranklin.waitUntilDone(), karla.waitUntilDone()]);

/** Canvas text is painted once and never reflows, so anything drawing labels onto a canvas waits for the faces first. */
export function useFontsLoaded() {
  const [loaded, setLoaded] = useState(false);
  const [handle] = useState(() => delayRender("Waiting for fonts before painting canvas text"));
  useEffect(() => {
    allFaces.then(() => {
      setLoaded(true);
      continueRender(handle);
    });
  }, [handle]);
  return loaded;
}
