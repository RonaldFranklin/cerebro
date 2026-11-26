import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, type ApiClient, type ValidationReport, type WriteWarning } from "./api";
import { Markdown, TechnicalText } from "./Markdown";
import "./inbox.css";

interface Item {
  id: string; kind: string; status: string; revision: number; source_agent: string; target: string | null; conflict: boolean;
}
interface Preview extends Item {
  base_label: string; stale_base: boolean;
  candidate: {id: string; title: string; body: string; lang: string} | null;
  validation: ValidationReport | null; historical_validation: ValidationReport | null;
  decision: {kind: string; target: string | null; reason: string | null} | null;
  diff: { fields: Record<string, {before: unknown; after: unknown}>; body: string; truncated: boolean };
}
interface Outcome { status?: string; outcome?: string; commit?: string | null; warnings?: WriteWarning[]; revision?: number }
type Action = "amend" | "reject" | "approve" | "resume";
const reviewable = (status: string) => status === "needs_owner" || status === "curated";
const label = (value: string) => value.replaceAll("_", " ");
const path = (id: string) => "/api/inbox/" + encodeURIComponent(id);

function Validation({report}: {report: ValidationReport | null}) {
  if (!report) return <p>Validation is unavailable for this revision.</p>;
  return <><p>{report.ok ? "Validation passed." : "Validation failed."}</p>
    {report.violations.length > 0 && <ul>{report.violations.map((violation, index) => <li key={index}>
      <code>{violation.code}</code>{violation.field && <> · <code>{violation.field}</code></>}
      {violation.line != null && <> · line {violation.line}</>} · {violation.severity}
    </li>)}</ul>}</>;
}

function valueText(value: unknown) {
  if (value === null || value === undefined) return "—";
  return Array.isArray(value) ? value.join(", ") || "[]" : String(value);
}

function Changes({detail}: {detail: Preview}) {
  const {candidate, diff} = detail;
  const baseLabel = detail.base_label === "current" ? "Current note" : detail.base_label === "empty" ? "Empty base" : "Base note";
  return <div className="inbox-changes">
    {detail.base_label === "empty" && <p className="inbox-mark">New note</p>}
    {detail.stale_base && <p role="alert">The target changed after submission.</p>}
    <section><h3>Frontmatter changes</h3>
      {Object.keys(diff.fields).length ? <div className="inbox-table"><table><thead><tr><th>Field</th><th>{baseLabel}</th><th>Candidate</th></tr></thead>
        <tbody>{Object.entries(diff.fields).map(([key, pair]) => <tr key={key}><th scope="row"><code>{key}</code></th>
          <td>{valueText(pair.before)}</td><td>{valueText(pair.after)}</td></tr>)}</tbody></table></div> : <p>No frontmatter changes.</p>}
    </section>
    <section><h3>Markdown changes</h3>
      {diff.body ? <pre className="inbox-diff" translate="no" aria-label="Markdown diff"><code>{diff.body.replace(/\n$/, "").split("\n").map((line, i) =>
        <span key={i} className={line.startsWith("+") ? "diff-add" : line.startsWith("-") ? "diff-remove" : undefined}>{line}{"\n"}</span>)}</code></pre> : <p>No Markdown changes.</p>}
      {diff.truncated && <p>Only part of this diff is shown.</p>}
    </section>
    {candidate && <details><summary>Candidate preview</summary><Markdown body={candidate.body} lang={candidate.lang}/></details>}
    <section><h3>Validation</h3><Validation report={detail.validation}/>
      {detail.historical_validation && <details><summary>Submission validation</summary><Validation report={detail.historical_validation}/></details>}
    </section>
    <section><h3>Curator decision</h3>{detail.decision ? <p>{label(detail.decision.kind)}
      {detail.decision.target && <> · <code>{detail.decision.target}</code></>}
      {detail.decision.reason && <> · <TechnicalText text={detail.decision.reason}/></>}</p> : <p>No recorded decision.</p>}</section>
  </div>;
}

function Review({api, item}: {api: ApiClient; item: Item}) {
  const client = useQueryClient();
  const [mode, setMode] = useState<"amend" | "reject" | null>(null);
  const [draft, setDraft] = useState("");
  const [reason, setReason] = useState("");
  const [feedback, setFeedback] = useState("");
  const [commit, setCommit] = useState("");
  const [warnings, setWarnings] = useState<WriteWarning[]>([]);
  const [validation, setValidation] = useState<ValidationReport>();
  const [locked, setLocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [finished, setFinished] = useState(false);
  const request = useRef<AbortController | null>(null);
  const formRevision = useRef(item.revision);
  const canReview = reviewable(item.status);
  const preview = useQuery({
    queryKey:["inbox-detail", item.id], enabled:canReview && !finished,
    queryFn:({signal}) => api.read<Preview>(path(item.id), signal),
  });
  const detail = canReview && !finished ? preview.data : undefined;
  useEffect(() => () => request.current?.abort(), []);
  async function refresh() {
    await client.invalidateQueries({queryKey:["inbox-list"]});
    await client.invalidateQueries({queryKey:["inbox-detail",item.id]});
  }
  async function reload() {
    if (request.current) return;
    setMode(null); setDraft(""); setReason(""); setValidation(undefined); setFeedback("");
    setBusy(true);
    try {
      await client.refetchQueries({queryKey:["inbox-list"]}, {throwOnError:true});
      await client.refetchQueries({queryKey:["inbox-detail",item.id]}, {throwOnError:true});
      setLocked(false);
    } catch { setFeedback("Reload failed. Try again."); }
    finally { setBusy(false); }
  }
  async function act(action: Action) {
    if (locked || finished || busy || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setFeedback(""); setValidation(undefined);
    const revision = mode ? formRevision.current : detail?.revision ?? item.revision;
    const body = action === "amend" ? {revision, body:draft} : action === "reject" ? {revision, reason} : {revision};
    try {
      const outcome = await api.write<Outcome>(path(item.id) + "/" + action, "POST", body, controller.signal);
      setMode(null); setDraft(""); setReason("");
      setWarnings(outcome.warnings ?? []); setCommit(outcome.commit ?? "");
      if (action === "amend") {
        setFeedback("Amendment saved.");
        setLocked(true);
        await refresh();
        const fresh = client.getQueryState<Preview>(["inbox-detail",item.id]);
        setLocked(fresh?.status !== "success" || fresh.data?.revision !== outcome.revision);
      } else {
        setFinished(true);
        await client.cancelQueries({queryKey:["inbox-detail",item.id]});
        client.removeQueries({queryKey:["inbox-detail",item.id]});
        setFeedback(action === "reject" ? "Proposal rejected." : outcome.outcome === "already_applied" ? "Already applied." : "Applied");
        await client.invalidateQueries();
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 409) {
        setLocked(true); setFeedback("This revision can no longer be used. Reload before reviewing again.");
      } else if (error instanceof ApiError && error.status === 422) {
        setValidation(error.validation); setFeedback("The change did not pass validation.");
        if (action !== "amend" && action !== "reject") setLocked(true);
      } else {
        setLocked(true); setFeedback("The result could not be confirmed. Reload before retrying.");
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      request.current = null;
    }
  }
  return <section className="inbox-review" aria-labelledby="review-heading">
    <header><h2 id="review-heading">Review proposal</h2><code>{item.id}</code></header>
    <dl className="proposal-meta"><div><dt>Agent</dt><dd translate="no">{item.source_agent}</dd></div><div><dt>Status</dt><dd>{label(item.status)}</dd></div>
      <div><dt>Revision</dt><dd>{detail?.revision ?? item.revision}</dd></div></dl>
    {feedback && <p role="status">{feedback}</p>}
    {commit && <p>Commit <code>{commit}</code></p>}
    {warnings.map(warning => <p key={warning.code} role="status">{warning.message}</p>)}
    {validation && <div role="alert"><Validation report={validation}/></div>}
    {locked && <button disabled={busy} onClick={() => void reload()}>Reload proposal</button>}
    {canReview && !finished && (preview.isError ? <p role="alert">Preview is unavailable. <button onClick={() => void refresh()}>Refresh preview</button></p> :
      !detail ? <p role="status">Loading preview…</p> : <>
        <Changes detail={detail}/>
        {!mode && <div className="inbox-actions">
          <button className="primary-btn" disabled={busy || locked || detail.stale_base || detail.conflict || !detail.validation?.ok || !detail.candidate}
            onClick={() => void act("approve")}>Approve and apply</button>
          {["create","update"].includes(detail.kind) && <button disabled={busy || locked || detail.stale_base || detail.conflict}
            onClick={() => {formRevision.current = detail.revision; setDraft(detail.candidate?.body ?? ""); setMode("amend");}}>Amend Markdown</button>}
          <button disabled={busy || locked} onClick={() => {formRevision.current = detail.revision; setMode("reject");}}>Reject proposal</button>
        </div>}
        {mode === "amend" && <form onSubmit={e => {e.preventDefault(); void act("amend");}}>
          <label>Replacement Markdown<textarea translate="no" value={draft} onChange={e=>setDraft(e.target.value)} rows={8}/></label>
          <button className="primary-btn" disabled={busy || locked || !draft.trim()}>Save amendment</button>
          <button type="button" disabled={busy} onClick={()=>{setMode(null); setDraft(""); setValidation(undefined);}}>Cancel amendment</button>
        </form>}
        {mode === "reject" && <form onSubmit={e=>{e.preventDefault(); void act("reject");}}>
          <label>Reason for rejection<textarea value={reason} maxLength={1000} onChange={e=>setReason(e.target.value)} rows={2}/></label>
          <button disabled={busy || locked || !reason.trim()}>Confirm rejection</button>
          <button type="button" disabled={busy} onClick={()=>{setMode(null); setReason("");}}>Cancel rejection</button>
        </form>}
      </>)}
    {!finished && item.status === "approved" && <button className="primary-btn" disabled={busy || locked} onClick={()=>void act("resume")}>Resume application</button>}
    {!finished && !canReview && item.status !== "approved" && <p>No reviewable candidate is available.</p>}
  </section>;
}

export function InboxScreen({api}: {api: ApiClient}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const list = useQuery({queryKey:["inbox-list"],queryFn:({signal})=>api.read<{items:Item[]}>("/api/inbox",signal)});
  const items = list.data?.items ?? [];
  const visible = status ? items.filter(item => item.status === status) : items;
  const item = items.find(row => row.id === selected);
  return <article className="inbox-view"><header className="page-heading"><h1>Inbox</h1></header>
    {list.isPending ? <p role="status">Loading proposals…</p> : list.isError ? <p role="alert">Inbox is unavailable. <button onClick={()=>void list.refetch()}>Retry</button></p> : <>
      <div className="inbox-toolbar"><label>Status <select value={status} onChange={e=>{setStatus(e.target.value); setSelected(null);}}>
        <option value="">All statuses</option>{[...new Set([...items.map(row=>row.status), ...(status ? [status] : [])])].sort().map(s=><option key={s} value={s}>{label(s)}</option>)}
      </select></label><span role="status">{visible.length} {visible.length === 1 ? "proposal" : "proposals"}</span></div>
      <div className={item ? "inbox-layout has-selection" : "inbox-layout"}><section className="inbox-list" aria-label="Proposals">
        {visible.length ? <ul>{visible.map(row=><li key={row.id}><button type="button" className={selected === row.id ? "current" : ""}
          aria-label={`Review ${row.id}`} aria-pressed={selected === row.id} onClick={()=>setSelected(row.id)}>
          <strong>{label(row.kind)}{row.target && <> · <span translate="no">{row.target}</span></>}</strong>
          <span>{label(row.status)} · <span translate="no">{row.source_agent}</span></span><code>{row.id}</code>
        </button></li>)}</ul> : <p>No proposals match this view.</p>}
      </section>{item && <Review key={item.id} item={item} api={api}/>}</div>
    </>}
  </article>;
}
