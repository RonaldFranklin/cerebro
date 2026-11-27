import type { ValidationReport } from "./api";
import type { CheckResult, useDraftCheck } from "./draftCheck";

type Violation = ValidationReport["violations"][number];
type State = "passed" | "warning" | "failed" | "unchecked";

const groups = {
  budget: ["body_over_soft_limit", "body_over_hard_limit", "hub_over_budget", "core_over_budget"],
  links: ["broken_link", "hub_not_found", "hub_not_a_hub", "superseded_by_cycle", "hub_chain_cycle", "hub_chain_too_deep"],
  sensitivity: ["link_to_higher_sensitivity", "core_restricted"],
  secrets: ["secret_detected"],
};
const grouped = new Set(Object.values(groups).flat());

/** The screen is stricter than the save: every error blocks, and so does a broken link (decision D1). */
function blocks(violation: Violation) {
  return violation.severity === "error" || violation.code === "broken_link";
}

export function passes(result: CheckResult) {
  return result.base_current === true && !result.validation.violations.some(blocks);
}

function describe(violation: Violation) {
  const words = violation.code.replaceAll("_", " ");
  const place = [violation.field, violation.line ? `line ${violation.line}` : ""].filter(Boolean).join(", ");
  return words[0].toUpperCase() + words.slice(1) + (place ? ` (${place})` : "");
}

const stateNames: Record<State, string> = {passed: "Passed", warning: "Warning", failed: "Failed", unchecked: "Not checked"};

function Item({name, state, children}: {name: string; state: State; children: React.ReactNode}) {
  return <li data-state={state}>
    <h3><span aria-hidden="true" className="check-mark">{state === "passed" ? "✓" : state === "failed" ? "✕" : state === "warning" ? "!" : "·"}</span>
      {name}<span className="visually-hidden">: {stateNames[state]}</span></h3>
    {children}
  </li>;
}

function Checks({result, priority}: {result: CheckResult; priority: string}) {
  const violations = result.validation.violations;
  // Without a budget no note could be built from the fields, so only the schema was checked.
  const built = result.budget !== null;
  const found = (codes: string[]) => violations.filter(v => codes.includes(v.code));
  const state = (list: Violation[]): State => !built ? "unchecked" : list.some(blocks) ? "failed" : list.length ? "warning" : "passed";
  const lines = (list: Violation[], clean: string) => !built ? <p>Fix the fields first.</p>
    : list.length ? <ul>{list.map((violation, index) => <li key={index}>{describe(violation)}</li>)}</ul> : <p>{clean}</p>;
  const schema = violations.filter(v => !grouped.has(v.code));
  const links = found(groups.links), sensitivity = found(groups.sensitivity);
  const budget = result.budget;
  const badLines = result.link_lines.length > 0 && <p>{result.link_lines.length === 1 ? "Line" : "Lines"} {result.link_lines.join(", ")} of the body</p>;
  return <ul className="checks">
    <Item name="Schema" state={schema.some(blocks) ? "failed" : schema.length ? "warning" : "passed"}>
      {schema.length ? <ul>{schema.map((violation, index) => <li key={index}>{describe(violation)}</li>)}</ul> : <p>Fields are valid.</p>}
    </Item>
    <Item name={`Word budget · ${priority}`} state={state(found(groups.budget))}>
      {budget ? <>
        <progress aria-label="Words used" max={budget.hard} value={Math.min(budget.words, budget.hard)}/>
        <p>{budget.words.toLocaleString("en")} / {budget.soft ? `${budget.soft.toLocaleString("en")} soft · ` : ""}{budget.hard.toLocaleString("en")} hard</p>
      </> : <p>Fix the fields first.</p>}
    </Item>
    <Item name="Links" state={state(links)}>{lines(links, "Every link has a target.")}{links.length > 0 && badLines}</Item>
    <Item name="Sensitivity" state={state(sensitivity)}>
      {lines(sensitivity, "No link to a more sensitive note.")}{sensitivity.length > 0 && badLines}</Item>
    <Item name="Secret scan" state={state(found(groups.secrets))}>{lines(found(groups.secrets), "Nothing found (best-effort).")}</Item>
    <Item name="Base version" state={result.base_current === null ? "unchecked" : result.base_current ? "passed" : "failed"}>
      <p>{result.base_current === null ? "Fix the fields first." : result.base_current
        ? "No one changed this note since you opened it." : "The stored note changed since you opened it."}</p>
    </Item>
  </ul>;
}

export function ValidationPanel({check, priority}: {check: ReturnType<typeof useDraftCheck>; priority: string}) {
  const summary = check.status === "checking" ? "Checking the draft…" : check.status === "unavailable"
    ? "The draft could not be checked. Saving is blocked." : passes(check.result) ? "All checks pass." : "Saving is blocked while a check fails.";
  return <aside className="editor-validation" aria-label="Validation">
    <h2>Validation</h2>
    <p role="status">{summary}</p>
    {check.status === "unavailable" && <button type="button" onClick={check.retry}>Retry checks</button>}
    {check.status === "done" && <Checks result={check.result} priority={priority}/>}
    <p className="editor-author">Commits as <strong>owner</strong>. If an agent writes this note meanwhile, you compare both versions before saving.</p>
  </aside>;
}
