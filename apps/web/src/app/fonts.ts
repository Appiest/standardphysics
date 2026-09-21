import { Atkinson_Hyperlegible_Mono, Karla, Libre_Franklin } from "next/font/google";

const karla = Karla({ subsets: ["latin"], variable: "--font-karla" });
const libreFranklin = Libre_Franklin({ subsets: ["latin"], variable: "--font-libre-franklin" });
/** next/font has no size metrics for this family, so it gets a plain monospace fallback instead of a generated one. */
const atkinsonMono = Atkinson_Hyperlegible_Mono({
  subsets: ["latin"],
  variable: "--font-atkinson-mono",
  adjustFontFallback: false,
  fallback: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
});

export const appFontVariables = `${karla.variable} ${libreFranklin.variable} ${atkinsonMono.variable}`;
