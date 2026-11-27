import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ApiClient } from "./api";
import { notePath } from "./Reader";
import { useVisibleGraph } from "./visibleGraph";
import "./health.css";

type Severity = "error" | "warning";
interface Finding {code: string; severity: Severity; field: string | null; subject: string; message: string}
interface HealthReport {
  ok: boolean;
  notes_checked: number;
  search_index_checked: boolean;
  issues: Finding[];
}

const descriptions: Record<string,string> = {
  index_stale: "Index revision differs from the repository",
  search_index_stale: "Search entry differs from the repository",
  graph_index_stale: "Graph entry differs from the repository",
  secret_detected: "Secret scanner match",
  broken_link: "Broken link",
  orphan_note: "Note has no incoming or outgoing links",
  changes_quarantined: "Quarantined uncommitted changes",
  review_overdue: "Note review is overdue",
  body_over_soft_limit: "Body exceeds the recommended size",
  body_over_hard_limit: "Body exceeds the maximum size",
  hub_over_budget: "Hub exceeds its word budget",
  core_over_budget: "Core notes exceed their combined word budget",
  note_file_excluded: "Committed file is not a valid note",
  note_file_blocking: "Note file differs from the last commit or cannot be read",
};
const nonNoteFindings = new Set(["index_stale","core_over_budget","note_file_excluded","note_file_blocking"]);

interface Card {id: string; name: string; value: string; detail: string; tone?: "ok" | "warning" | "error"}

function cards(report: HealthReport): Card[] {
  const codes = new Set(report.issues.map(issue => issue.code));
  // Files that block the scan stop the checks before any index is compared.
  const blocked = codes.has("note_file_blocking") && !report.search_index_checked;
  const index = (id: string, name: string, differs: boolean, [same, other]: [string, string], checked = true): Card =>
    blocked || !checked ? {id, name, value: "Not checked", detail: "Compared with the content repository", tone: "warning"}
      : {id, name, value: differs ? other : same, detail: "Compared with the content repository", tone: differs ? "error" : "ok"};
  const quarantined = report.issues.filter(issue => issue.code === "changes_quarantined").length;
  const files = codes.has("note_file_blocking") || codes.has("note_file_excluded");
  return [
    index("revision", "Repository revision", codes.has("index_stale"), ["Matches indexes", "Out of date"]),
    index("graph", "Graph index", codes.has("graph_index_stale"), ["Entries match", "Entries differ"]),
    index("search", "Search index", codes.has("search_index_stale"), ["Entries match", "Entries differ"], report.search_index_checked),
    {id: "content", name: "Content repository", value: files ? "Needs attention" : "Clean", tone: files ? "error" : "ok",
      detail: `${report.notes_checked} ${report.notes_checked === 1 ? "note" : "notes"} checked`},
    {id: "quarantine", name: "Quarantine", tone: quarantined ? "warning" : "ok",
      value: quarantined ? `${quarantined} ${quarantined === 1 ? "folder" : "folders"}` : "None",
      detail: quarantined ? "Plaintext — review and remove by hand" : "No uncommitted changes were set aside"},
  ];
}

const checkedFormat = new Intl.DateTimeFormat(undefined, {hour: "2-digit", minute: "2-digit", timeZoneName: "short"});

export function HealthScreen({api}: {api:ApiClient}) {
  const report = useQuery({queryKey:["health"],queryFn:({signal})=>api.read<HealthReport>("/api/health",signal)});
  const graph = useVisibleGraph(api,true);
  const visibleIds = useMemo(() => new Set(graph.isSuccess ? graph.data.nodes.map(note => note.id) : []), [graph.data,graph.isSuccess]);
  const [severity,setSeverity] = useState("all");
  const data = report.data;
  if (report.isPending) return <p role="status">Loading health report…</p>;
  if (report.isError || !data) return <section className="health-view"><h1>Health</h1>
    <p role="alert">The health report is unavailable.</p><button onClick={()=>void report.refetch()}>Retry</button></section>;

  const ordered = [...data.issues].sort((a,b) => Number(b.severity === "error") - Number(a.severity === "error"));
  const findings = severity === "all" ? ordered : ordered.filter(finding => finding.severity === severity);
  const errors = ordered.filter(finding => finding.severity === "error").length;
  const warnings = ordered.length - errors;
  const summary = !data.ok ? "Errors found" : warnings ? "Warnings found"
    : data.search_index_checked ? "All systems normal" : "Partly checked";
  const tone = !data.ok ? "error" : warnings || !data.search_index_checked ? "warning" : "ok";

  return <article className="health-view">
    <header className="health-heading"><div className="health-title"><h1>Health</h1>
      <strong className="health-pill" data-tone={tone}>{summary}</strong></div>
      <p>Checked <time dateTime={new Date(report.dataUpdatedAt).toISOString()}>{checkedFormat.format(report.dataUpdatedAt)}</time></p>
      <button type="button" disabled={report.isFetching} onClick={()=>{void report.refetch(); void graph.refetch();}}>
        {report.isFetching ? "Refreshing…" : "Refresh report"}</button></header>
    <section className="health-cards" aria-label="Report summary">
      {cards(data).map(card => <div key={card.id} data-card={card.id} data-tone={card.tone}>
        <h2>{card.name}</h2><strong>{card.value}</strong><p>{card.detail}</p></div>)}
    </section>
    {!data.search_index_checked && data.notes_checked > 0 && <p className="health-note">The repository exceeds the search comparison limit.</p>}
    <section className="health-findings"><header><div><h2>Findings</h2><p>{errors} {errors === 1 ? "error" : "errors"} · {warnings} {warnings === 1 ? "warning" : "warnings"}</p></div>
      <label>Severity <select value={severity} onChange={event=>setSeverity(event.target.value)}>
        <option value="all">All severities</option><option value="error">Errors</option><option value="warning">Warnings</option>
      </select></label></header>
      {!findings.length ? <p className="health-empty">{ordered.length ? "No findings match this severity." : "No findings in the completed checks."}</p>
        : <ul>{findings.map((finding,index) => {
          const link = !nonNoteFindings.has(finding.code) && visibleIds.has(finding.subject) &&
            finding.subject.length <= 80 && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(finding.subject);
          const title = descriptions[finding.code] ?? finding.code.replaceAll("_"," ");
          return <li key={index}><span className="health-severity" data-tone={finding.severity}>{finding.severity === "error" ? "Error" : "Warning"}</span>
            <div className="health-detail"><strong>{title}</strong><code translate="no">{finding.code}</code>
              {finding.message && <p className="health-message">{finding.message}</p>}</div>
            <div className="health-subject">{link ? <a translate="no" href={notePath(finding.subject,true)}>{finding.subject}</a>
              : <span translate="no">{finding.subject}</span>}{finding.field && <small translate="no">{finding.field}</small>}</div></li>;
        })}</ul>}
    </section>
  </article>;
}
