import { autocompletion, closeCompletion, completionKeymap, type CompletionContext } from "@codemirror/autocomplete";
import { defaultKeymap, history as editHistory, historyKeymap } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { EditorView, keymap } from "@codemirror/view";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { ApiError, type ApiClient, type ValidationReport, type WriteWarning } from "./api";
import { Markdown, TechnicalText } from "./Markdown";
import { notePath, type Note } from "./Reader";
import { useVisibleGraph } from "./visibleGraph";
import { useDraftCheck } from "./draftCheck";
import { passes, ValidationPanel } from "./ValidationPanel";
import "./editor.css";

const labels = {
  title:"Title", summary:"Summary", type:"Type", area:"Area", priority:"Priority", status:"Status",
  sensitivity:"Sensitivity", lang:"Language", hub:"Hub", related:"Related notes",
  superseded_by:"Superseded by", review_by:"Review by", body:"Markdown body",
};
type Field = keyof typeof labels;
type Draft = Record<Field,string>;
const fields = Object.keys(labels) as Field[];
const choices = {
  priority:["P0","P1","P2","P3"], sensitivity:["public","internal","restricted"],
  status:["current","proposed","applied","verified","superseded","archived"],
};

function draftFrom(note: Note): Draft {
  return {title:note.title, summary:note.summary, type:note.type, area:note.area, priority:note.priority,
    sensitivity:note.sensitivity,status:note.status,lang:note.lang,hub:note.hub ?? "",related:note.related.join(", "),
    superseded_by:note.superseded_by ?? "",review_by:note.review_by ?? "",body:note.body};
}

function payload(draft: Draft) {
  return {...draft, related:draft.related.split(",").map(s => s.trim()).filter(Boolean),
    hub:draft.hub.trim() || null,superseded_by:draft.superseded_by.trim() || null,review_by:draft.review_by || null};
}

type Editable = Note & {content_hash: string};
function comparisonText(field: Field, value: string, ids: ReadonlySet<string>) {
  if (field === "title" || field === "summary") return <p><TechnicalText text={value || "—"} visibleIds={ids}/></p>;
  return <pre translate="no">{value || "—"}</pre>;
}

function complete(note: Note | undefined, id: string): note is Editable {
  return note?.id === id && note.section === null && /^[0-9a-f]{64}$/.test(note.content_hash ?? "");
}

function BodyEditor({value, onChange, ids}: {value:string; onChange:(body:string)=>void; ids:ReadonlySet<string>}) {
  const parent = useRef<HTMLDivElement>(null);
  const instance = useRef<EditorView | null>(null);
  const options = useRef({value, onChange, ids});
  useLayoutEffect(() => {
    if (options.current.ids !== ids && instance.current) closeCompletion(instance.current);
    options.current = {value, onChange, ids};
    const view = instance.current;
    if (view && view.state.doc.toString() !== value) {
      view.dispatch({changes:{from:0,to:view.state.doc.length,insert:value}});
    }
  }, [value, onChange, ids]);

  useLayoutEffect(() => {
    if (!parent.current) return;
    function wikilinks(context: CompletionContext) {
      const match = context.matchBefore(/\[\[[a-z0-9-]*$/);
      if (!match) return null;
      const prefix = match.text.slice(2);
      const line = context.state.doc.lineAt(context.pos);
      const suffix = /^[a-z0-9-]*\]{0,2}/.exec(line.text.slice(context.pos - line.from))?.[0] ?? "";
      const matches = Array.from(options.current.ids).filter(id => id.startsWith(prefix)).sort().slice(0, 80);
      return {from:match.from + 2, to:context.pos + suffix.length, options:matches.map(id => ({label:id, apply:id + "]]"}))};
    }
    const nonce = document.querySelector<HTMLMetaElement>('meta[name="cerebro-style-nonce"]')?.content ?? "";
    const view = new EditorView({
      parent:parent.current, doc:options.current.value,
      extensions:[
        EditorView.cspNonce.of(nonce), markdown(), editHistory(),
        autocompletion({override:[wikilinks]}), keymap.of([...completionKeymap, ...defaultKeymap, ...historyKeymap]),
        EditorView.lineWrapping,
        EditorView.contentAttributes.of({"aria-label":"Markdown body",translate:"no",spellcheck:"false"}),
        EditorView.updateListener.of(update => {
          if (update.docChanged) options.current.onChange(update.state.doc.toString());
        }),
      ],
    });
    instance.current = view;
    return () => {instance.current = null; view.destroy();};
  }, []);
  return <div className="owner-code" ref={parent} translate="no"/>;
}

interface SaveResult { content_hash:string; commit:string; validation:ValidationReport; warnings:WriteWarning[] }

function friendly(code: string) {
  const words = code.replaceAll("_", " ");
  return words[0]?.toUpperCase() + words.slice(1);
}

function EditForm({api, initial, history, reload}: {api:ApiClient;initial:Editable;history:boolean;reload:()=>Promise<Editable>}) {
  const cache = useQueryClient();
  const [base,setBase] = useState(() => draftFrom(initial));
  const [draft,setDraft] = useState(base);
  const [hash,setHash] = useState(initial.content_hash);
  const [preview,setPreview] = useState(false);
  const [busy,setBusy] = useState(false);
  const [conflict,setConflict] = useState(false);
  const [latest,setLatest] = useState<Editable | null>(null);
  const [message,setMessage] = useState("");
  const [validation,setValidation] = useState<ValidationReport | null>(null);
  const [warnings,setWarnings] = useState<WriteWarning[]>([]);
  const graph = useVisibleGraph(api, history);
  const ids = useMemo(() => new Set(graph.isSuccess ? graph.data.nodes.map(node => node.id) : []), [graph.data,graph.isSuccess]);

  const incoming = latest ? draftFrom(latest) : null;
  const dirty = fields.some(field => base[field] !== draft[field]);
  const check = useDraftCheck(api, initial.id, JSON.stringify({expected_hash:hash, note:payload(draft)}));
  const changedElsewhere = check.status === "done" && check.result.base_current === false;
  useEffect(() => { if (changedElsewhere) setConflict(true); }, [changedElsewhere]);
  const ready = dirty && !busy && !conflict && check.status === "done" && passes(check.result);
  function update(field: Field, value: string) {
    setDraft(current => current[field] === value ? current : {...current,[field]:value});
    setValidation(null); setWarnings([]); setMessage("");
  }

  async function save() {
    if (!ready) {
      if (dirty && !busy) setMessage("Saving is blocked until the checks pass.");
      return;
    }
    setBusy(true); setMessage(""); setValidation(null); setWarnings([]);
    try {
      const result = await api.write<SaveResult>("/api/notes/" + encodeURIComponent(initial.id), "PUT", {
        expected_hash:hash, note:payload(draft),
      });
      setHash(result.content_hash); setBase(draft); setValidation(result.validation);
      setWarnings(result.warnings); setMessage("Changes saved.");
      void cache.invalidateQueries();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
      else if (error instanceof ApiError && error.status === 422) {
        setValidation(error.validation ?? null);
        setMessage("Review the note fields and try again.");
      } else {
        setMessage(error instanceof ApiError && error.status === 413
          ? "The note exceeds the request size limit." : "Save could not be confirmed. Your draft is still here.");
      }
    } finally {setBusy(false);}
  }

  const submit = useRef(save);
  useLayoutEffect(() => { submit.current = save; });
  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if (event.key.toLowerCase() !== "s" || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
      event.preventDefault();
      if (event.repeat) return;
      void submit.current();
    }
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);

  async function inspect() {
    setBusy(true); setMessage("");
    try {setLatest(await reload());}
    catch {setMessage("Could not reload the complete stored note. Your draft is still here.");}
    finally {setBusy(false);}
  }
  function resolve(keep: boolean) {
    if (!latest) return;
    const stored = draftFrom(latest);
    const merged = {...stored};
    if (keep) for (const field of fields) if (draft[field] !== base[field]) merged[field] = draft[field];
    setBase(stored); setHash(latest.content_hash); setDraft(merged); setLatest(null); setConflict(false);
    setValidation(null); setWarnings([]);
    setMessage(keep ? "Stored changes loaded. Review your draft before saving." : "Stored note loaded.");
  }

  function problems(field: Field | null) {
    return validation?.violations.filter(v => field ? v.field === field : !v.field || !Object.hasOwn(labels,v.field))
      .map((v,i) => <p className="field-problem" key={i}>{friendly(v.code)}{v.line ? ` · line ${v.line}` : ""}</p>);
  }
  function control(field: Exclude<Field,"body">) {
    const options = choices[field as keyof typeof choices];
    const id = "edit-" + field;
    return <div className={"editor-field field-" + field} key={field}>
      <label htmlFor={id}>{labels[field]}</label>
      {options ? <select id={id} value={draft[field]} onChange={e=>update(field,e.target.value)} translate="no">
        {options.map(option=><option key={option}>{option}</option>)}</select>
        : field === "summary" ? <textarea id={id} value={draft[field]} rows={2} onChange={e=>update(field,e.target.value)}/>
        : <input id={id} type={field === "review_by" ? "date" : "text"} value={draft[field]}
            translate={field === "title" ? "yes" : "no"} placeholder={field === "related" ? "Comma-separated IDs" : undefined}
            onChange={e=>update(field,e.target.value)}/>}
      {problems(field)}
    </div>;
  }

  return <article className="owner-editor">
    <header className="editor-head">
      <div><p>Editing <a href={notePath(initial.id,history)} translate="no">{initial.id}</a></p>
        <h1><TechnicalText text={base.title} visibleIds={ids}/></h1></div>
      {dirty && <span className="editor-unsaved">Unsaved changes</span>}
      <a className="editor-cancel" href={notePath(initial.id,history)}>Cancel</a>
      <button className="primary-btn" onClick={() => void save()} disabled={!ready} aria-keyshortcuts="Control+S Meta+S">Save &amp; commit</button>
    </header>
    {message && <p className="editor-feedback" role="status">{message}</p>}
    {warnings.map(w=><p className="editor-feedback" key={w.code}>{w.message}</p>)}
    {problems(null)}
    {conflict && <section className="editor-conflict" aria-label="Conflict recovery">
      <h2>The stored note changed</h2><p>Your draft is preserved. Compare it with the stored note before saving.</p>
      {!incoming ? <button disabled={busy} onClick={() => void inspect()}>Reload stored note</button> :
        <><div className="comparison-list">{fields.filter(f => incoming[f] !== base[f] || draft[f] !== base[f]).map(f =>
          <div key={f}><h3>{labels[f]}</h3><div className="comparison-pair">
            <div><strong>Stored</strong>{comparisonText(f,incoming[f],ids)}</div>
            <div><strong>Your draft</strong>{comparisonText(f,draft[f],ids)}</div>
          </div></div>)}</div>
          <button onClick={()=>resolve(true)}>Keep my changes</button> <button onClick={()=>resolve(false)}>Use stored note</button>
        </>}
    </section>}
    <div className="editor-columns"><div inert={busy} className="editor-panels">
      <section className="editor-writing">
        <div className="editor-section-heading"><h2>Note content</h2><div className="editor-switch">
          <button aria-pressed={!preview} onClick={()=>setPreview(false)}>Write</button>
          <button aria-pressed={preview} onClick={()=>setPreview(true)}>Preview</button></div></div>
        {control("title")}{control("summary")}
        <div className="editor-field"><span>Markdown body</span>
          <div hidden={preview}><BodyEditor value={draft.body} onChange={value=>update("body",value)} ids={ids}/></div>
          {preview && <div className="editor-preview"><Markdown body={draft.body} lang={draft.lang} history={history} visibleIds={ids}/></div>}
          {problems("body")}
        </div>
      </section>
      <aside className="editor-properties"><h2>Properties</h2><div className="editor-property-grid">
        {(["type","area","priority","status","sensitivity","lang","hub","related","superseded_by","review_by"] as const).map(control)}
      </div></aside>
    </div>
    <ValidationPanel check={check} priority={draft.priority}/></div>
  </article>;
}

export function EditorScreen({api,id,history}: {api:ApiClient;id:string;history:boolean}) {
  const source = useQuery({queryKey:["editor-source",id], refetchOnMount:"always", queryFn:({signal})=>api.read<Note>("/api"+notePath(id,true), signal)});
  // An explicit reload supplies a candidate new base. Background cache refreshes never replace a live draft.
  const [opened, setOpened] = useState<Editable | null>(null);
  useLayoutEffect(() => {
    const note = source.data;
    if (source.isSuccess && source.isFetchedAfterMount && complete(note,id)) setOpened(current=>current?.id === id ? current : note);
  }, [source.data,source.isSuccess,source.isFetchedAfterMount,id]);
  if (opened?.id === id) return <EditForm key={id} api={api} initial={opened} history={history} reload={async () => {
    const result = await source.refetch({throwOnError:true});
    if (!complete(result.data,id)) throw new Error("Incomplete source");
    return result.data;
  }}/>;
  if (source.isPending || source.isFetching) return <p role="status">Loading note…</p>;
  return <section className="pending-view"><h1>Edit note</h1><p role="alert">The complete note is required for editing.</p>
    <button onClick={()=>void source.refetch()}>Retry</button><a href={notePath(id,history)}>Read note</a></section>;
}
