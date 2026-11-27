/** Fixed colors of the areas the theme names. */
const fixed: Record<string, string> = {projects: "#5ce1e6", infrastructure: "#f5a524"};
/** Distinct hues that also differ in lightness, so areas stay apart without color vision. */
const palette = ["#a78bfa", "#6ee7a8", "#f58fb8", "#f4e27a", "#5b8def", "#e8dcc4", "#e0603f", "#b7e4ff", "#9aa86a", "#c65fc9"];

/** The color of ``value`` among all the values of its dimension: unnamed ones take the palette in sorted order. */
export function colorFor(value: string, domain: Iterable<string>): string {
  if (Object.hasOwn(fixed, value)) return fixed[value];
  const others = [...new Set([...domain, value])].filter(item => !Object.hasOwn(fixed, item)).sort();
  return palette[others.indexOf(value) % palette.length];
}

export function sizeFor(priority: string): number {
  switch (priority) {
    case "P0": return 13;
    case "P1": return 10;
    case "P2": return 6.5;
    default: return 4.5;
  }
}
