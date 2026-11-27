/** The owner's agent credentials and their recent use. The service sends no id, secret or hash. */
export interface Token {
  client: string;
  ceiling: string;
  scopes: string[];
  state: "active" | "revoked" | "expired";
  usage_known: boolean;
  last_used: string | null;
  activity: number[];
}

// This locale writes the local date and time as "YYYY-MM-DD, HH:mm".
const moment = new Intl.DateTimeFormat("en-CA", {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit",
  minute: "2-digit", hourCycle: "h23"});
const NOW = 2 * 60_000;

function LastUsed({token, checkedAt}: {token: Token; checkedAt: string}) {
  if (!token.usage_known) return <>Unavailable</>;
  if (!token.last_used) return <>Never</>;
  const used = new Date(token.last_used);
  // Measured against the server's check time, not this browser's clock.
  const now = new Date(checkedAt).getTime() - used.getTime() < NOW;
  return <time dateTime={token.last_used} data-tone={now ? "ok" : undefined}>
    {now ? "now" : moment.format(used).replace(",", "")}</time>;
}

function Activity({counts}: {counts: number[]}) {
  const total = counts.reduce((sum, count) => sum + count, 0);
  const highest = Math.max(1, ...counts);
  const points = counts.map((count, hour) => `${hour * 5},${22 - count / highest * 20}`).join(" ");
  return <span className="token-activity">
    <svg viewBox="0 0 115 24" width="115" height="24" aria-hidden="true" focusable="false">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/></svg>
    <span>{total} {total === 1 ? "request" : "requests"}</span>
  </span>;
}

function Table({name, tokens, checkedAt}: {name: string; tokens: Token[]; checkedAt: string}) {
  return <div className="token-scroll" tabIndex={0} role="group" aria-label={`${name} tokens table`}>
    <table aria-label={name}>
      <thead><tr><th scope="col">Client</th><th scope="col">Ceiling</th><th scope="col">Scopes</th>
        <th scope="col">Last used</th><th scope="col">Activity · 24 h</th></tr></thead>
      <tbody>{tokens.map((token, index) => <tr key={index}>
        <th scope="row" translate="no">{token.client}</th>
        <td translate="no" data-tone={token.ceiling === "restricted" ? "warning" : undefined}>{token.ceiling}</td>
        <td translate="no">{token.scopes.join(", ")}</td>
        <td><LastUsed token={token} checkedAt={checkedAt}/></td>
        <td>{token.usage_known ? <Activity counts={token.activity}/> : "Unavailable"}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

export function TokenTable({tokens, checkedAt}: {tokens: Token[]; checkedAt: string}) {
  const group = (state: Token["state"]) => tokens.filter(token => token.state === state);
  const active = group("active");
  return <section className="health-tokens" aria-labelledby="tokens-title">
    <h2 id="tokens-title">Agent tokens</h2>
    {active.length ? <Table name="Active" tokens={active} checkedAt={checkedAt}/> : <p>No active tokens.</p>}
    {([["revoked", "Revoked"], ["expired", "Expired"]] as const).map(([state, name]) => {
      const rows = group(state);
      return rows.length > 0 && <details key={state}><summary>{name} · {rows.length}</summary>
        <Table name={name} tokens={rows} checkedAt={checkedAt}/></details>;
    })}
  </section>;
}
