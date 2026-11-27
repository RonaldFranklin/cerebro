import { lazy, Suspense, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { type ApiClient, type Overview } from "./api";
import { BrowserSession } from "./session";
import { Login } from "./Login";
import { OverviewScreen, Shell } from "./Shell";
import { ReaderScreen } from "./Reader";
import { requestSearch, SearchScreen } from "./Search";
import "./fonts.css";
import "./app.css";

const GraphScreen = lazy(() => import("./GraphScreen"));

const EditorScreen = lazy(() => import("./Editor").then(module => ({default: module.EditorScreen})));
const RecentChangesScreen = lazy(() => import("./RecentChanges").then(module => ({default: module.RecentChangesScreen})));
const HealthScreen = lazy(() => import("./Health").then(module => ({default: module.HealthScreen})));

function Status({children, action, label}: {children: ReactNode; action?: () => void; label?: string}) {
  return <main className="session-status"><h1>Cérebro</h1><p role="status">{children}</p>
    {action && <button type="button" onClick={action}>{label}</button>}</main>;
}

function Home({api}: {api: ApiClient}) {
  const result = useQuery({queryKey: ["overview"], queryFn: ({ signal }) => api.read<Overview>("/api/overview", signal)});
  if (result.isPending) return <p role="status">Loading overview…</p>;
  if (result.isError) return <section><h1>Overview</h1><p role="alert">Overview is unavailable.</p>
    <button onClick={() => void result.refetch()}>Retry</button></section>;
  return <OverviewScreen data={result.data}/>;
}

const routeTitles: Record<string,string> = {"/graph":"Graph", "/search":"Search", "/health":"Health", "/changes":"Recent changes"};

function address() { return location.pathname + location.search + location.hash; }

function App() {
  const [manager] = useState(() => new BrowserSession());
  const state = useSyncExternalStore(manager.subscribe, manager.snapshot);
  const [route, setRoute] = useState(address);
  const [searchRequest, setSearchRequest] = useState(0);
  const url = new URL(route, location.origin);
  const path = url.pathname;

  useEffect(() => {
    manager.start();
    const pop = () => setRoute(address());
    const resume = (event: PageTransitionEvent) => { if (event.persisted) void manager.restore(); };
    const hide = (event: PageTransitionEvent) => { if (event.persisted) flushSync(manager.suspend); };
    const visible = () => { if (document.visibilityState === "visible") void manager.revalidate(); };
    window.addEventListener("popstate", pop);
    window.addEventListener("pageshow", resume);
    window.addEventListener("pagehide", hide);
    window.addEventListener("focus", manager.revalidate);
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.removeEventListener("popstate", pop);
      window.removeEventListener("pageshow", resume);
      window.removeEventListener("pagehide", hide);
      window.removeEventListener("focus", manager.revalidate);
      document.removeEventListener("visibilitychange", visible);
      manager.stop();
    };
  }, [manager]);

  function navigate(next: string) {
    if (next === route || !next.startsWith("/") || next.startsWith("//")) return;
    history.pushState(null, "", next);
    setRoute(address());
    document.getElementById("workspace-main")?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
  switch (state.kind) {
    case "checking": return <Status>Checking session…</Status>;
    case "offline": return <Status action={manager.restore} label="Retry">The service is unavailable.</Status>;
    case "signing-out": return <Status>Signing out…</Status>;
    case "logout-failed": return <Status action={manager.signOut} label="Retry sign out">Sign-out could not be confirmed.</Status>;
    case "anonymous": return <Login onSubmit={manager.signIn} notice={state.notice}/>;
    case "active": {
      const home = path === "/" || path === "/login";
      const title = routeTitles[path] ?? (path.startsWith("/notes/") ? "Note reader" : path.startsWith("/editor/") ? "Owner editor" : "Page unavailable");
      const denied = state.session.ceiling !== "restricted" && (path === "/changes" || path === "/health" || path.startsWith("/editor/"));
      return <QueryClientProvider key={state.generation} client={state.queries}>
        {state.checking && <Status>Checking session…</Status>}
        <div hidden={state.checking}><Shell path={home ? "/" : path} ceiling={state.session.ceiling} onNavigate={navigate}
          onSearch={query => { requestSearch(state.queries, query); setSearchRequest(count => count + 1); navigate("/search"); }}
          onLogout={manager.signOut}>
          {denied ? <section className="pending-view"><h1>{title}</h1>
            <p>This view requires a restricted owner session. This session is limited to {state.session.ceiling}.</p>
            <p>Open it through the private address, or ask the instance operator to allow restricted browser access here.</p>
            <a href="/">Back to overview</a></section> : home ? <Home api={state.api}/> :
            path === "/changes" ? <Suspense fallback={<p role="status">Loading recent changes…</p>}><RecentChangesScreen api={state.api}/></Suspense> :
            path === "/health" ? <Suspense fallback={<p role="status">Loading health report…</p>}><HealthScreen api={state.api}/></Suspense> :
            path.startsWith("/editor/") ? <Suspense fallback={<p role="status">Loading editor…</p>}><EditorScreen
              key={path} api={state.api} id={path.slice("/editor/".length)} history={url.searchParams.get("history") === "true"}/></Suspense> :
            path === "/graph" ? <Suspense fallback={<p role="status">Loading graph…</p>}><GraphScreen api={state.api}/></Suspense> :
            path === "/search" ? <SearchScreen key={searchRequest} api={state.api}/> :
            path.startsWith("/notes/") ? <ReaderScreen api={state.api} id={path.slice("/notes/".length)}
              history={url.searchParams.get("history") === "true"} section={url.searchParams.get("section")}/> : <section className="pending-view"><h1>{title}</h1>
            <p>This page could not be found.</p>
            <a href="/">Back to overview</a></section>}
        </Shell></div>
      </QueryClientProvider>;
    }
  }
}

createRoot(document.getElementById("root")!).render(<App/>);
