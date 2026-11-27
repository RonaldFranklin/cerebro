import { useRef, useState } from "react";
import type { FormEvent } from "react";

export function Brand() {
  return <span className="brand"><svg className="brand-mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
    <path d="M12 3c5 0 9 3.4 9 8s-3.6 7-6.5 7c-1.5 0-1.7 3-4.5 3-4 0-7-4-7-9s4-9 9-9z" />
    <g fill="currentColor" stroke="none"><circle cx="9" cy="9" r="1.7" /><circle cx="15" cy="9.5" r="1.7" /><circle cx="11" cy="14.5" r="1.7" /></g>
  </svg><span className="brand-name">Cérebro</span></span>;
}

/** Decorative dots only. No workspace data is drawn on the sign-in page. */
function Synapses() {
  const dots: [number, number, number, string][] = [[31, 24, 4, "#5ce1e6"], [45, 18, 4, "#5ce1e6"], [59, 23, 5, "#f5a524"],
    [32, 43, 4, "#5ce1e6"], [63, 35, 3, "#f5a524"], [27, 62, 3, "#6ee7a8"], [70, 66, 3, "#a78bfa"]];
  return <svg className="login-synapses" aria-hidden="true" focusable="false">
    {dots.map(([x, y, r, color]) => <circle key={`${x}-${y}`} cx={`${x}%`} cy={`${y}%`} r={r} fill={color} opacity=".45"/>)}
  </svg>;
}

export function Login({ onSubmit, notice }: { onSubmit: (password: string) => Promise<void>; notice?: string }) {
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !password) return;
    setPending(true);
    setError("");
    try {
      await onSubmit(password);
    } catch {
      setPassword("");
      setError("Sign-in failed. Check your password and try again.");
      input.current?.focus();
    } finally {
      setPending(false);
    }
  }
  return (
    <main className="login-page">
      <Synapses />
      <form className="login-form" onSubmit={submit} aria-labelledby="login-title">
        <h1 id="login-title"><Brand /></h1>
        <p>One brain for all your AI agents. Sign in as the owner.</p>
        <label htmlFor="owner-password">Owner password</label>
        <input id="owner-password" type="password" ref={input} value={password}
          onChange={event => setPassword(event.target.value)} autoComplete="current-password" required />
        {(error || notice) && <p className="form-notice" role="status">{error || notice}</p>}
        <button className="primary-btn" type="submit" disabled={pending}>{pending ? "Signing in…" : "Sign in"}</button>
        <p className="login-limits">Session ends after 30 minutes idle or 8 hours. Five attempts per 15 minutes.</p>
      </form>
    </main>
  );
}
