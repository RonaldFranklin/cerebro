import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { type ApiClient, type Overview } from "./api";
import { BrowserSession } from "./session";
import { Login } from "./Login";
import { OverviewScreen, Shell } from "./Shell";
import "./app.css";

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

const routeTitles: Record<string,string> = {"/graph":"Graph", "/search":"Search", "/inbox":"Inbox", "/health":"Health"};

function App() {
  const [manager] = useState(() => new BrowserSession());
  const state = useSyncExternalStore(manager.subscribe, manager.snapshot);
  const [path, setPath] = useState(location.pathname);

  useEffect(() => {
    manager.start();
    const pop = () => setPath(location.pathname);
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
    if (next === path || !next.startsWith("/") || next.startsWith("//")) return;
    history.pushState(null, "", next);
    setPath(location.pathname);
  }
  switch (state.kind) {
    case "checking": return <Status>Checking session…</Status>;
    case "offline": return <Status action={manager.restore} label="Retry">The service is unavailable.</Status>;
    case "signing-out": return <Status>Signing out…</Status>;
    case "logout-failed": return <Status action={manager.signOut} label="Retry sign out">Sign-out could not be confirmed.</Status>;
    case "anonymous": return <Login onSubmit={manager.signIn} notice={state.notice}/>;
    case "active": {
      const home = path === "/" || path === "/login";
      const title = routeTitles[path] ?? (path.startsWith("/notes/") ? "Note reader" : "Owner editor");
      const denied = state.session.ceiling !== "restricted" && (path === "/inbox" || path === "/health" || path.startsWith("/editor/"));
      return <QueryClientProvider key={state.generation} client={state.queries}>
        {state.checking && <Status>Checking session…</Status>}
        <div hidden={state.checking}><Shell path={home ? "/" : path} ceiling={state.session.ceiling} onNavigate={navigate} onLogout={manager.signOut}>
          {home ? <Home api={state.api}/> : <section className="pending-view"><h1>{title}</h1>
            <p>{denied ? "This view requires the private listener." : "This view is not yet available."}</p>
            <a href="/">Back to overview</a></section>}
        </Shell></div>
      </QueryClientProvider>;
    }
  }
}

createRoot(document.getElementById("root")!).render(<App/>);
