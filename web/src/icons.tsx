/** Inline strokes drawn with the current color. SVG attributes only: the CSP refuses style attributes. */
const shapes = {
  graph: <><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="8" r="2.5" /><circle cx="11" cy="18" r="2.5" /><path d="M8.4 6.5l7.2 1M7.1 8.3l2.8 7.4M16.6 10l-4.2 6" /></>,
  notes: <><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4M9 12h6M9 16h6" /></>,
  changes: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.5 2" /></>,
  health: <path d="M3 12h4l2.5-6 5 12 2.5-6h4" />,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" /></>,
  lock: <><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 018 0v3" /></>,
};

export function Icon({ name }: { name: keyof typeof shapes }) {
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{shapes[name]}</svg>;
}
