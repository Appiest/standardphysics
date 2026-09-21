"use client";

/* eslint-disable complexity */

// Guard against React 19 development mode / Next.js Turbopack User Timing instrumentation crashes.
// When React logs component renders or server IO, it calls performance.measure with a detail object
// containing devtools properties. If serializing detail exceeds V8's structured clone memory limits,
// the browser throws: DOMException [DataCloneError]: Data cannot be cloned, out of memory.
// Catching it and retrying without detail prevents fatal app crashes in development.
if (typeof window !== "undefined" && window.performance && typeof window.performance.measure === "function") {
  const nativeMeasure = window.performance.measure.bind(window.performance);

  window.performance.measure = function (
    measureName: string,
    startOrMeasureOptions?: string | PerformanceMeasureOptions,
    endMark?: string,
  ): PerformanceMeasure {
    try {
      if (typeof startOrMeasureOptions === "string" || startOrMeasureOptions === undefined) {
        return nativeMeasure(measureName, startOrMeasureOptions, endMark);
      }
      return nativeMeasure(measureName, startOrMeasureOptions);
    } catch (err) {
      if (err instanceof DOMException && err.name === "DataCloneError") {
        if (typeof startOrMeasureOptions === "object" && startOrMeasureOptions !== null) {
          const safeOptions: PerformanceMeasureOptions = { ...startOrMeasureOptions };
          delete safeOptions.detail;
          try {
            return nativeMeasure(measureName, safeOptions);
          } catch {
            return undefined as unknown as PerformanceMeasure;
          }
        }
        return undefined as unknown as PerformanceMeasure;
      }
      throw err;
    }
  };
}
