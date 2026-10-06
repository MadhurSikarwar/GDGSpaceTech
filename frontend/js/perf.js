// Keeps a page responsive on a weak GPU. The landing page draws a live globe behind scrolling text; if frames are
// consistently slow the page itself feels slow, so ask for a cheaper picture, one step at a time.
//
// It only judges real rendering: a gap of a quarter of a second or more is a paused tab or a stall (the browser was
// busy elsewhere), not slow drawing, and does not count. A step needs `windows` slow windows in a row, so the heavy
// first seconds (imagery tiles arriving) and one unlucky moment do not trigger it.
export function createGovernor({ steps = 3, slowMs = 34, frames = 45, windows = 2, onStep } = {}) {
  let level = 0;
  let n = 0;
  let sum = 0;
  let slow = 0;
  return {
    get level() { return level; },
    // feed the time between two rendered frames (ms); returns true when it stepped down
    frame(deltaMs) {
      if (!(deltaMs > 0) || deltaMs >= 250) return false;
      sum += deltaMs;
      n += 1;
      if (n < frames) return false;
      const avg = sum / n;
      n = 0;
      sum = 0;
      slow = avg > slowMs ? slow + 1 : 0;
      if (slow >= windows && level < steps) {
        slow = 0;
        level += 1;
        if (onStep) onStep(level, avg);
        return true;
      }
      return false;
    },
    reset() { n = 0; sum = 0; slow = 0; },
  };
}
