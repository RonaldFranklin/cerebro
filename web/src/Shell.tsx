import type { MouseEvent, ReactNode } from "react";

import type { Ceiling, Overview } from "./api";
import { Brand } from "./Login";

const destinations = [
  { path: "/", label: "Overview", icon: "overview" },
  { path: "/search", label: "Search", icon: "search" },
  { path: "/graph", label: "Graph", icon: "graph" },
  { path: "/inbox", label: "Inbox", icon: "inbox", private: true },
  { path: "/health", label: "Health", icon: "health", private: true },
];

type ShellProps = {
  path: string;
  ceiling: Ceiling;
  onNavigate: (path: string) => void;
  onLogout: () => void;
  children: ReactNode;
};

export function Shell({ path, ceiling, onNavigate, onLogout, children }: ShellProps) {
  function follow(event: MouseEvent<HTMLDivElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
    if (!(anchor instanceof HTMLAnchorElement) || !anchor.getAttribute("href")?.startsWith("/") ||
        anchor.target || anchor.hasAttribute("download") || anchor.origin !== window.location.origin) return;
    event.preventDefault();
    onNavigate(anchor.pathname + anchor.search + anchor.hash);
  }

  return <div className="workspace" onClick={follow}>
    <a className="skip-link" href="#workspace-main">Skip to content</a>
    <aside className="workspace-rail">
      <a className="brand-home" href="/" aria-label="Cérebro home"><Brand /></a>
      <nav className="workspace-nav" aria-label="Workspace">
        {destinations.filter(item => !item.private || ceiling === "restricted").map(item =>
          <a key={item.path} href={item.path} aria-current={path.split(/[?#]/, 1)[0] === item.path ? "page" : undefined}>
            <i className={`nav-icon icon-${item.icon}`} aria-hidden="true" />{item.label}
          </a>
        )}
      </nav>
      <div className="rail-session">
        <span className="session-caption">Session access</span>
        <strong><i className="session-light" aria-hidden="true" />{ceiling}</strong>
        <button className="sign-out" onClick={onLogout}>Sign out</button>
      </div>
    </aside>
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
