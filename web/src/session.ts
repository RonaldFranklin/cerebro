import { QueryClient } from "@tanstack/react-query";
import { ApiClient, ApiError, currentSession, login, logout, type Session } from "./api";

const IDLE_MS = 30 * 60_000;
const ABSOLUTE_MS = 8 * 60 * 60_000;

type State =
  | { kind: "checking" | "offline" | "signing-out" | "logout-failed" }
  | { kind: "anonymous"; notice?: string }
  | { kind: "active"; generation: number; checking: boolean; session: Session; queries: QueryClient; api: ApiClient };

export class BrowserSession {
  private generation = 0;
  private probe?: AbortController;
  private absoluteDeadline = Infinity;
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
    this.probe?.abort();
    this.probe = undefined;
    this.lifetime.abort();
    this.lifetime = new AbortController();
    window.clearTimeout(this.idleTimer);
    window.clearTimeout(this.absoluteTimer);
    this.absoluteDeadline = Infinity;
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

  private scheduleDeadline(session: Session, started: number): boolean {
    const seconds = session.absolute_remaining_seconds;
    if (!Number.isFinite(seconds) || seconds > ABSOLUTE_MS / 1000) {
      this.reset({ kind: "offline" });
      return false;
    }
    this.absoluteDeadline = Math.min(this.absoluteDeadline, started + seconds * 1000);
    const remaining = this.absoluteDeadline - performance.now();
    if (remaining <= 0) {
      this.expire();
      return false;
    }
    window.clearTimeout(this.absoluteTimer);
    this.absoluteTimer = window.setTimeout(this.expire, remaining);
    return true;
  }

  private activate(session: Session, started: number) {
    if (!["internal", "restricted"].includes(session.ceiling) || !session.csrf_token) {
      this.reset({ kind: "offline" });
      return;
    }
    this.reset({ kind: "checking" });
    if (!this.scheduleDeadline(session, started)) return;
    this.set({
      kind: "active", checking: false, generation: ++this.generation, session,
      queries: new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } }),
      api: new ApiClient(session, this.lifetime.signal, this.expire, this.activity),
    });
    this.activity();
  }

  restore = async () => {
    if (this.pendingLogout) return;
    this.reset({ kind: "checking" });
    const signal = this.lifetime.signal;
    const started = performance.now();
    try {
      const session = await currentSession(signal);
      if (!signal.aborted) this.activate(session, started);
    } catch (error) {
      if (signal.aborted) return;
      this.reset(error instanceof ApiError && error.status === 401 ? { kind: "anonymous" } : { kind: "offline" });
    }
  };

  revalidate = async () => {
    if (this.state.kind !== "active") return;
    this.probe?.abort();
    const probe = new AbortController();
    this.probe = probe;
    this.set({ ...this.state, checking: true });
    const signal = probe.signal;
    const started = performance.now();
    try {
      const session = await currentSession(signal);
      if (signal.aborted || this.state.kind !== "active") return;
      if (session.csrf_token !== this.state.session.csrf_token || session.ceiling !== this.state.session.ceiling) {
        this.activate(session, started);
      } else if (this.scheduleDeadline(session, started)) {
        this.activity();
        this.set({ ...this.state, checking: false });
      }
    } catch (error) {
      if (!signal.aborted) {
        if (error instanceof ApiError && error.status === 401) this.expire();
        else this.reset({ kind: "offline" });
      }
    } finally {
      if (this.probe === probe) this.probe = undefined;
    }
  };

  signIn = async (password: string) => {
    const signal = this.lifetime.signal;
    const started = performance.now();
    const session = await login(password, signal);
    if (!signal.aborted) {
      this.activate(session, started);
      this.channel?.postMessage("changed");
    }
  };

  signOut = async () => {
    if (this.state.kind === "active") this.pendingLogout = this.state.session.csrf_token;
    if (!this.pendingLogout || this.state.kind === "signing-out") return;
    const retry = this.state.kind === "logout-failed";
    this.reset({ kind: "signing-out" });
    const signal = this.lifetime.signal;
    try {
      if (retry) {
        const session = await currentSession(signal);
        signal.throwIfAborted();
        this.pendingLogout = session.csrf_token;
      }
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
