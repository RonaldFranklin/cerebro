import { QueryClient } from "@tanstack/react-query";
import { ApiClient, ApiError, currentSession, login, logout, type Session } from "./api";

const IDLE_MS = 30 * 60_000;
const ABSOLUTE_MS = 8 * 60 * 60_000;

type State =
  | { kind: "checking" | "offline" | "signing-out" | "logout-failed" }
  | { kind: "anonymous"; notice?: string }
  | { kind: "active"; generation: number; session: Session; queries: QueryClient; api: ApiClient };

export class BrowserSession {
  private generation = 0;
  private state: State = { kind: "checking" };
  private readonly listeners = new Set<() => void>();
  private lifetime = new AbortController();
  private idleTimer?: number;
  private absoluteTimer?: number;
  private pendingLogout?: string;
  private channel?: BroadcastChannel;

  snapshot = (): State => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  start() {
    this.channel = new BroadcastChannel("cerebro-session");
    this.channel.onmessage = () => this.restore();
    void this.restore();
  }

  stop() {
    this.channel?.close();
    this.reset({kind: "checking"});
  }

  private set(state: State) {
    this.state = state;
    for (const listener of this.listeners) listener();
  }

  private reset(state: State) {
    this.lifetime.abort();
    this.lifetime = new AbortController();
    window.clearTimeout(this.idleTimer);
    window.clearTimeout(this.absoluteTimer);
    if (this.state.kind === "active") {
      void this.state.queries.cancelQueries();
      this.state.queries.clear();
    }
    this.set(state);
  }

  private expire = () => this.reset({ kind: "anonymous", notice: "Your session expired. Sign in again." });

  private activity = () => {
    window.clearTimeout(this.idleTimer);
    this.idleTimer = window.setTimeout(this.expire, IDLE_MS);
  };

  private activate(session: Session) {
    if (!["internal", "restricted"].includes(session.ceiling) || !session.csrf_token) {
      this.reset({ kind: "offline" });
      return;
    }
    this.reset({ kind: "checking" });
    this.set({
      kind: "active", generation: ++this.generation, session,
      queries: new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } }),
      api: new ApiClient(session, this.lifetime.signal, this.expire, this.activity),
    });
    this.activity();
    this.absoluteTimer = window.setTimeout(this.expire, ABSOLUTE_MS);
  }

  restore = async () => {
    if (this.pendingLogout) return;
    this.reset({ kind: "checking" });
    const signal = this.lifetime.signal;
    try {
      const session = await currentSession(signal);
      if (!signal.aborted) this.activate(session);
    } catch (error) {
      if (signal.aborted) return;
      this.reset(error instanceof ApiError && error.status === 401 ? { kind: "anonymous" } : { kind: "offline" });
    }
  };

  revalidate = async () => {
    if (this.state.kind !== "active") return;
    const signal = this.lifetime.signal;
    try {
      const session = await currentSession(signal);
      if (signal.aborted || this.state.kind !== "active") return;
      if (session.csrf_token !== this.state.session.csrf_token || session.ceiling !== this.state.session.ceiling) {
        this.activate(session);
      } else this.activity();
    } catch (error) {
      if (!signal.aborted) {
        if (error instanceof ApiError && error.status === 401) this.expire();
        else this.reset({ kind: "offline" });
      }
    }
  };

  signIn = async (password: string) => {
    const signal = this.lifetime.signal;
    const session = await login(password, signal);
    if (!signal.aborted) {
      this.activate(session);
      this.channel?.postMessage("changed");
    }
  };

  signOut = async () => {
    if (this.state.kind === "active") this.pendingLogout = this.state.session.csrf_token;
    if (!this.pendingLogout || this.state.kind === "signing-out") return;
    this.reset({ kind: "signing-out" });
    const signal = this.lifetime.signal;
    try {
      await logout(this.pendingLogout, signal);
    } catch (error) {
      if (signal.aborted) return;
      if (!(error instanceof ApiError && error.status === 401)) {
        this.set({ kind: "logout-failed" });
        return;
      }
    }
    if (signal.aborted) return;
    this.pendingLogout = undefined;
    this.reset({ kind: "anonymous" });
    this.channel?.postMessage("changed");
  };

  suspend = () => {
    if (!this.pendingLogout) this.reset({ kind: "checking" });
  };
}
