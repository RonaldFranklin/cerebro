import { useEffect, useState } from "react";

import type { ApiClient, ValidationReport } from "./api";

export interface CheckResult {
  validation: ValidationReport;
  budget: {words: number; soft: number | null; hard: number} | null;
  link_lines: number[];
  base_current: boolean | null;
}

type Check = {status: "checking"} | {status: "unavailable"; retry: () => void} | {status: "done"; result: CheckResult};

const PAUSE = 400;

/** Ask the service to check the draft after a pause in typing. A result only ever answers the draft it was asked for. */
export function useDraftCheck(api: ApiClient, id: string, body: string): Check {
  const [attempt, setAttempt] = useState(0);
  const key = `${attempt}\n${body}`;
  const [answer, setAnswer] = useState<{key: string; result: CheckResult | null} | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api.write<CheckResult>(`/api/notes/${encodeURIComponent(id)}/validate`, "POST", JSON.parse(body), controller.signal)
        .then(result => { if (!controller.signal.aborted) setAnswer({key, result}); },
          () => { if (!controller.signal.aborted) setAnswer({key, result: null}); });
    }, PAUSE);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [api, id, body, key]);
  if (answer?.key !== key) return {status: "checking"};
  return answer.result ? {status: "done", result: answer.result}
    : {status: "unavailable", retry: () => setAttempt(count => count + 1)};
}
