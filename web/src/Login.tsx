import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

export function Brand() {
  return <span className="brand"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>Cérebro</span>;
}

/** Decorative geometry only. No workspace data is drawn on the sign-in page. */
function Constellation() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const draw = () => {
      const { width, height } = canvas.getBoundingClientRect();
      if (!width || !height) return;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      const scale = Math.min(width / 560, height / 330);
      ctx.translate(width / 2, height / 2);
      ctx.scale(scale, scale);
      const points = [
        [-196, 34], [-162, -52], [-135, 105], [-98, 12], [-72, -106], [-39, 69], [-24, -41],
        [14, 125], [35, 15], [38, -125], [83, -65], [105, 85], [146, -1], [187, -94], [205, 61],
      ];
      const edges = [[0,1],[0,2],[0,3],[1,3],[1,4],[2,3],[2,5],[3,4],[3,5],[3,6],[4,6],
        [4,9],[5,6],[5,7],[5,8],[6,8],[6,9],[6,10],[7,8],[7,11],[8,10],[8,11],[8,12],
        [9,10],[9,13],[10,12],[10,13],[11,12],[11,14],[12,13],[12,14]];
      for (const [a, b] of edges) {
        ctx.beginPath(); ctx.moveTo(points[a][0], points[a][1]); ctx.lineTo(points[b][0], points[b][1]);
        ctx.strokeStyle = a === 3 || b === 8 ? "#719d94" : "#355652"; ctx.lineWidth = 1; ctx.stroke();
      }
      points.forEach(([x, y], i) => {
        const main = i === 3 || i === 8 || i === 10;
        if (main) {
          ctx.beginPath(); ctx.arc(x, y, 12, 0, 2 * Math.PI);
          ctx.strokeStyle = "#53776e"; ctx.stroke();
        }
        ctx.beginPath(); ctx.arc(x, y, main ? 5 : 3.5, 0, 2 * Math.PI);
        ctx.fillStyle = i === 10 || i === 13 ? "#b5a5ce" : main ? "#b0ddbd" : "#75988f";
        ctx.fill();
      });
    };
    const resize = new ResizeObserver(draw);
    resize.observe(canvas);
    draw();
    return () => resize.disconnect();
  }, []);
  return <canvas className="constellation" ref={ref} aria-hidden="true" />;
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
      <section className="login-intro" aria-label="About Cérebro">
        <Brand />
        <div className="intro-body">
          <Constellation />
          <h2>Knowledge<br />workspace</h2>
          <p>Browse your notes, explore their connections, and review agent proposals.</p>
        </div>
      </section>
      <section className="login-panel" aria-labelledby="login-title">
        <form className="login-form" onSubmit={submit}>
          <h1 id="login-title">Owner sign-in</h1>
          <p>Enter your password to open this workspace.</p>
          <label htmlFor="owner-password">Password</label>
          <input id="owner-password" type="password" ref={input} value={password}
            onChange={event => setPassword(event.target.value)} autoComplete="current-password" required />
          {(error || notice) && <p className="form-notice" role="status">{error || notice}</p>}
          <button className="primary-btn" type="submit" disabled={pending}>{pending ? "Signing in…" : "Sign in"}</button>
        </form>
      </section>
    </main>
  );
}
