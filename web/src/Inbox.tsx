import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, type ApiClient, type ValidationReport, type WriteWarning } from "./api";
import { Markdown, TechnicalText } from "./Markdown";
import { useVisibleGraph } from "./visibleGraph";
import "./inbox.css";

interface Item {
  id: string; kind: string; status: string; revision: number; source_agent: string; target: string | null; conflict: boolean;
}
interface Preview extends Item {
  base_label: string; stale_base: boolean;
  base: {body: string} | null;
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

function Changes({api, detail}: {api: ApiClient; detail: Preview}) {
  const graph = useVisibleGraph(api, false);
  const visibleIds = useMemo(() => new Set(graph.data?.nodes.map(node => node.id)), [graph.data]);
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
        <span key={i} className={line.startsWith("+") ? "diff-add" : line.startsWith("-") ? "diff-remove" : undefined}>{line}{"\n"}</span>)}</code></pre> : !diff.truncated && <p>No Markdown changes.</p>}
      {diff.truncated && <><p>Only part of this diff is shown.</p>
        <details><summary>Full Markdown comparison</summary>
          <h4>{baseLabel}</h4><pre className="inbox-diff" translate="no">{detail.base?.body ?? ""}</pre>
          <h4>Candidate</h4><pre className="inbox-diff" translate="no">{candidate?.body ?? ""}</pre>
        </details></>}
    </section>
    {candidate && <details><summary>Candidate preview</summary><Markdown body={candidate.body} lang={candidate.lang} visibleIds={visibleIds}/></details>}
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
  const [needsReload, setLocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [finished, setFinished] = useState(false);
  const request = useRef(false);
  const [inspected, setInspected] = useState<Preview>();
  const canReview = reviewable(item.status);
  const preview = useQuery({
    queryKey:["inbox-detail", item.id], enabled:canReview && !finished,
    queryFn:({signal}) => api.read<Preview>(path(item.id), signal),
  });
  const detail = canReview && !finished ? inspected : undefined;
  const changed = Boolean(detail && (item.revision > detail.revision || (preview.data && preview.data !== detail)));
  const locked = needsReload || changed;
  useEffect(() => {
    if (canReview && !finished) {
      if (preview.data) setInspected(current => current ?? preview.data);
    } else {
      setInspected(undefined);
      setMode(null); setDraft(""); setReason("");
    }
  }, [canReview, finished, preview.data]);
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
      const latest = client.getQueryData<{items: Item[]}>(["inbox-list"])?.items.find(row => row.id === item.id);
      if (latest && reviewable(latest.status)) {
        const current = await preview.refetch({throwOnError:true});
        setInspected(current.data);
      }
      setLocked(false);
    } catch { setFeedback("Reload failed. Try again."); }
    finally { setBusy(false); }
  }
  async function act(action: Action) {
    if (locked || finished || busy || request.current) return;
    request.current = true;
    setBusy(true); setFeedback(""); setValidation(undefined);
    const revision = action === "resume" ? item.revision : detail!.revision;
    const body = action === "amend" ? {revision, body:draft} : action === "reject" ? {revision, reason} : {revision};
    try {
      const outcome = await api.write<Outcome>(path(item.id) + "/" + action, "POST", body);
      setMode(null); setDraft(""); setReason("");
      setWarnings(outcome.warnings ?? []); setCommit(outcome.commit ?? "");
      if (action === "amend") {
        setFeedback("Amendment saved.");
        setLocked(true);
        await refresh();
      } else {
        setFinished(true);
        await client.cancelQueries({queryKey:["inbox-detail",item.id]});
        client.removeQueries({queryKey:["inbox-detail",item.id]});
        setFeedback(action === "reject" ? "Proposal rejected." : outcome.outcome === "already_applied" ? "Already applied." : "Applied");
        await client.invalidateQueries();
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (error instanceof ApiError && error.status === 409) {
        setLocked(true); setFeedback("This revision can no longer be used. Reload before reviewing again.");
        await client.invalidateQueries({queryKey:["inbox-list"]});
      } else if (error instanceof ApiError && error.status === 422) {
        setValidation(error.validation); setFeedback("The change did not pass validation.");
        if (action !== "amend" && action !== "reject") setLocked(true);
      } else {
        setLocked(true); setFeedback("The result could not be confirmed. Reload before retrying.");
        await client.invalidateQueries();
      }
    } finally {
      setBusy(false);
      request.current = false;
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
    {changed && <p role="alert">The proposal or its target has changed.</p>}
    {locked && <button disabled={busy} onClick={() => void reload()}>Reload proposal</button>}
    {canReview && !finished && (preview.isError ? <p role="alert">Preview is unavailable. <button onClick={() => void refresh()}>Refresh preview</button></p> :
      !detail ? <p role="status">Loading preview…</p> : <>
        <Changes api={api} detail={detail}/>
        {!mode && <div className="inbox-actions">
          <button className="primary-btn" disabled={busy || locked || detail.stale_base || detail.conflict || !detail.validation?.ok || !detail.candidate}
            onClick={() => void act("approve")}>Approve and apply</button>
          {["create","update"].includes(detail.kind) && <button disabled={busy || locked || detail.stale_base || detail.conflict}
            onClick={() => {setDraft(detail.candidate?.body ?? ""); setMode("amend");}}>Amend Markdown</button>}
          <button disabled={busy || locked} onClick={() => {setMode("reject");}}>Reject proposal</button>
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
  const client = useQueryClient();
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
      </select></label><button type="button" disabled={list.isFetching} onClick={()=>void Promise.all([list.refetch(), client.refetchQueries({queryKey:["inbox-detail"],type:"active"})])}>Refresh inbox</button><span role="status">{visible.length} {visible.length === 1 ? "proposal" : "proposals"}</span></div>
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
