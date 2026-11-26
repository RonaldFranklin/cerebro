const palette = ["#a8d5bb", "#c5afe5", "#e4ae8b", "#91c8e3", "#ebcc85", "#dd9db3", "#84cec8", "#b7c599", "#cda8b3", "#dbbb77"];

export function colorFor(value: string): string {
  let hash = 2166136261;
  for (const char of value) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  return palette[(hash >>> 0) % palette.length];
}

export function sizeFor(priority: string): number {
  switch (priority) {
    case "P0": return 13;
    case "P1": return 10;
    case "P2": return 7.5;
    default: return 5;
  }
}
