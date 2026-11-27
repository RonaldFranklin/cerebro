/** Words and dates shared by the owner's history views. */
export type Operation = "create" | "update" | "superseded";

export const operations: Record<Operation, string> = {create: "Created", update: "Updated", superseded: "Superseded"};

export function author(actor: string) {
  return actor === "owner" ? "Owner" : actor;
}

/** The local calendar date as ``YYYY-MM-DD``. */
export function dayKey(moment: Date) {
  return [moment.getFullYear(), moment.getMonth() + 1, moment.getDate()].map(part => String(part).padStart(2, "0")).join("-");
}
