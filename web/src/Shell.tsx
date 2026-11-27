import { useEffect, useRef, useState, type FormEvent, type MouseEvent, type ReactNode } from "react";

import type { Ceiling, Overview } from "./api";
import { Brand } from "./Login";
import { Icon } from "./icons";

const destinations = [
  { path: "/graph", label: "Graph", icon: "graph", sections: ["/graph"] },
  { path: "/search", label: "Notes", icon: "notes", sections: ["/search", "/notes", "/editor"] },
  { path: "/changes", label: "Recent changes", icon: "changes", sections: ["/changes"], restricted: true },
  { path: "/health", label: "Health", icon: "health", sections: ["/health"], restricted: true },
] as const;

/** Keys typed into a field or the editor belong to it, not to the search shortcut. */
function typing(target: EventTarget | null) {
  return target instanceof HTMLElement && (target.isContentEditable || target.matches("input, textarea, select"));
}

/** A tooltip stays dismissed until the pointer or focus leaves its destination. */
function useDismissedTooltip() {
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  useEffect(() => {
    if (!hovered) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setDismissed(hovered); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [hovered]);
  return (path: string) => {
    const enter = () => setHovered(path);
    const leave = () => { setHovered(null); setDismissed(null); };
    return {"data-dismissed": dismissed === path || undefined, onMouseEnter: enter, onFocus: enter, onMouseLeave: leave, onBlur: leave};
  };
}

function QuickSearch({ onSearch }: { onSearch: (query: string) => void }) {
  const [query, setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      // Some layouts type "/" with AltGr, which browsers may report as Control and Alt.
      const modified = !event.getModifierState("AltGraph") && (event.altKey || event.ctrlKey);
      if (event.key !== "/" || event.defaultPrevented || modified || event.metaKey || typing(event.target)) return;
      // A workspace hidden behind a session check cannot take focus.
      if (!input.current?.checkVisibility()) return;
      event.preventDefault();
      input.current.focus();
    }
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);
  function submit(event: FormEvent) {
    event.preventDefault();
    // A blank submit must not reset the search kept for the session.
    if (!query.trim()) return;
    onSearch(query.trim());
    setQuery("");
  }
  return <form className="quick-search" role="search" aria-label="Quick search" onSubmit={submit}>
    <Icon name="search" />
    {/* 256 UTF-16 units always fit the 1,024-byte search limit. */}
    <label htmlFor="quick-search">Quick search</label>
    <input ref={input} id="quick-search" type="search" aria-keyshortcuts="/" placeholder="Search notes, ids, summaries…"
      autoComplete="off" maxLength={256} value={query} onChange={event => setQuery(event.target.value)} />
    <kbd aria-hidden="true">/</kbd>
  </form>;
}

type ShellProps = {
  path: string;
  ceiling: Ceiling;
  onNavigate: (path: string) => void;
  onSearch: (query: string) => void;
  onLogout: () => void;
  children: ReactNode;
};

export function Shell({ path, ceiling, onNavigate, onSearch, onLogout, children }: ShellProps) {
  function follow(event: MouseEvent<HTMLDivElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
    if (!(anchor instanceof HTMLAnchorElement) || !anchor.getAttribute("href")?.startsWith("/") ||
        anchor.target || anchor.hasAttribute("download") || anchor.origin !== window.location.origin) return;
    event.preventDefault();
    onNavigate(anchor.pathname + anchor.search + anchor.hash);
  }
  const section = "/" + path.split(/[?#]/, 1)[0].split("/")[1];
  const tooltip = useDismissedTooltip();

  return <div className="workspace" onClick={follow}>
    <a className="skip-link" href="#workspace-main">Skip to content</a>
    <header className="topbar">
      <a className="brand-home" href="/" aria-label="Cérebro home" aria-current={section === "/" ? "page" : undefined}><Brand /></a>
      <QuickSearch onSearch={onSearch} />
      <div className="topbar-session">
        <span className="ceiling-badge"><Icon name="lock" /><span>{ceiling} · owner</span></span>
        <button type="button" className="sign-out" onClick={onLogout}>Sign out</button>
      </div>
    </header>
    <nav className="rail" aria-label="Workspace">
      {destinations.filter(item => !("restricted" in item) || ceiling === "restricted").map(item =>
        <a key={item.path} href={item.path} aria-label={item.label} {...tooltip(item.path)}
          aria-current={path.split(/[?#]/, 1)[0] === item.path ? "page" :
            (item.sections as readonly string[]).includes(section) ? "true" : undefined}>
          <Icon name={item.icon} />
          <span className="rail-tooltip" aria-hidden="true">{item.label}</span>
        </a>
      )}
    </nav>
    <main id="workspace-main" className="workspace-main" tabIndex={-1}>{children}</main>
  </div>;
}

function NoteList({ notes, empty }: { notes: Overview["hubs"]; empty: string }) {
  if (!notes.length) return <p className="empty-state">{empty}</p>;
  return <ul className="overview-notes">{notes.map(note =>
    <li key={note.id}>
      <a href={`/notes/${encodeURIComponent(note.id)}`}>{note.title}</a>
      <p>{note.summary}</p>
    </li>
  )}</ul>;
}

export function OverviewScreen({ data }: { data: Overview }) {
  return <article className="overview-view">
    <header className="page-heading">
      <h1>Overview</h1>
      <p>Your core notes, topic hubs, and knowledge areas.</p>
    </header>
    <div className="overview-content">
      <section className="overview-primary" aria-labelledby="core-heading">
        <div className="panel-heading"><h2 id="core-heading">Core notes</h2><span className="item-count">{data.core.length}</span></div>
        <NoteList notes={data.core} empty="No core notes are visible." />
        {data.core_truncated && <p className="empty-state">The overview includes a limited selection of core notes.</p>}
      </section>
      <section className="overview-areas" aria-labelledby="areas-heading">
        <div className="panel-heading"><h2 id="areas-heading">Areas</h2><span className="item-count">{data.areas.length}</span></div>
        {data.areas.length ? <ul className="area-list">{data.areas.map(area =>
          <li key={area}><span translate="no">{area}</span></li>
        )}</ul> : <p className="empty-state">No areas are visible.</p>}
      </section>
      <section className="overview-hubs" aria-labelledby="hub-heading">
        <div className="panel-heading"><h2 id="hub-heading">Topic hubs</h2><span className="item-count">{data.hubs.length}</span></div>
        <NoteList notes={data.hubs} empty="No topic hubs are visible." />
      </section>
    </div>
  </article>;
}
