import DOMPurify from "dompurify";
import { Marked, type Tokens } from "marked";
import { useLayoutEffect, useRef } from "react";
import "./markdown.css";

type Props = { body: string; lang: string; history?: boolean; linkWikiNotes?: boolean };
export type MarkdownHeading = { title: string; level: number };

function escapeHtml(text: string): string {
  return text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function externalUrl(value: string): string | null {
  if (!/^https?:\/\//i.test(value) || /[\s\\\p{Cc}\p{Cf}]/u.test(value)) return null;
  try {
    const url = new URL(value);
    if (url.username || url.password || url.origin === location.origin) return null;
    return url.href;
  } catch {
    return null;
  }
}

function validSection(title: string): boolean {
  return !/[\p{Cc}\p{Cf}]/u.test(title) && new TextEncoder().encode(title).length <= 256;
}

function parser(history: boolean, linkWikiNotes: boolean): Marked {
  return new Marked({
    gfm: true,
    renderer: {
      html({ raw }) { return escapeHtml(raw); },
      text(token) {
        // Marked flags text inside raw HTML contexts as already escaped. Keep it literal.
        if ("escaped" in token && token.escaped) return escapeHtml(token.text);
        return false;
      },
      image({ text }) { return escapeHtml(text); },
      link({ href, tokens }) {
        const label = this.parser.parseInline(tokens);
        const url = externalUrl(href);
        return url ? `<a href="${escapeHtml(url)}" rel="noreferrer" translate="no">${label}</a>` : label;
      },
      checkbox({ checked }) { return checked ? "☑ " : "☐ "; },
    },
    extensions: [{
      name: "wikilink", level: "inline",
      start(source: string) { return source.indexOf("[["); },
      tokenizer(source: string) {
        const match = /^\[\[([^\[\]\n#|]+)(?:#([^\[\]\n|]*))?\]\]/.exec(source);
        if (!match) return;
        return { type: "wikilink", raw: match[0], id: match[1].trim(), section: match[2]?.trim() || "" };
      },
      renderer(token: Tokens.Generic) {
        const id: string = token.id;
        const section: string = token.section;
        const label = escapeHtml(token.raw);
        if (!linkWikiNotes || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id) || id.length > 80 ||
            !validSection(section)) return label;
        const query = new URLSearchParams();
        if (section) query.set("section", section);
        if (history) query.set("history", "true");
        const href = `/notes/${id}${query.size ? "?" + query : ""}`;
        return `<a translate="no" href="${escapeHtml(href)}">${label}</a>`;
      },
    }],
  });
}

const lex = new Marked({ gfm: true });
/** The section endpoint addresses ATX headings by their original title, including inline Markdown. */
export function markdownHeadings(body: string): MarkdownHeading[] {
  const headings: MarkdownHeading[] = [];
  const seen = new Set<string>();
  for (const token of lex.lexer(body)) {
    if (token.type !== "heading") continue;
    const atx = /^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*(?:\n|$)/.exec(token.raw);
    if (!atx) continue;
    const title = atx[2].trim();
    const key = title.toLowerCase().replace(/\s+/g, " ");
    if (!title || !validSection(title) || seen.has(key)) continue;
    seen.add(key);
    headings.push({ title, level: atx[1].length });
  }
  return headings;
}

// Preserve technical tokens in prose. Authors use code spans for arbitrary shell syntax.
const shellCommand = /\b(?:git|pnpm|npm|uv|cerebro|docker|python3?)\s+(?:--?[\w-]+|[a-z][\w-]*)(?:\s+(?:--?[\w-]+|run|pytest|build|test|status|compose|install|--))*/;
const pathToken = /(?<![\w./~-])(?:~\/|\.\.?\/|\/|[\w.-]+\/)(?:[\w.-]+\/)*[\w.-]*[\w-]/;
const identifier = /\b[a-z0-9]+(?:-[a-z0-9]+)+\b|(?<!\w)--[a-z][\w-]*|\b[\w-]+\.(?:md|toml|yaml|yml|json|sh|py|ts|tsx|js|css|html|sqlite)\b/;
const technical = new RegExp([shellCommand, pathToken, identifier].map(pattern => pattern.source).join("|"), "g");

function markTechnicalText(fragment: DocumentFragment) {
  for (const element of fragment.querySelectorAll("pre, code, a")) element.setAttribute("translate", "no");
  const walker = document.createTreeWalker(fragment, NodeFilter.SHOW_TEXT);
  const textNodes: Text[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    if (!node.parentElement?.closest('[translate="no"]')) textNodes.push(node);
  }
  for (const node of textNodes) {
    const text = node.data;
    const replacement = document.createDocumentFragment();
    let cursor = 0;
    for (const match of text.matchAll(technical)) {
      const span = document.createElement("span");
      span.setAttribute("translate", "no");
      span.textContent = match[0];
      replacement.append(text.slice(cursor, match.index), span);
      cursor = match.index + match[0].length;
    }
    if (cursor) {
      replacement.append(text.slice(cursor));
      node.replaceWith(replacement);
    }
  }
}

export function Markdown({ body, lang, history = false, linkWikiNotes = true }: Props) {
  const container = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const html = parser(history, linkWikiNotes).parse(body, { async: false });
    const fragment = DOMPurify.sanitize(html, {
      ALLOWED_TAGS: [
        "p","h1","h2","h3","h4","h5","h6","strong","em","del","blockquote","hr","br",
        "ul","ol","li","pre","code","table","thead","tbody","tr","th","td","a",
      ],
      ALLOWED_ATTR: ["href","rel","translate","start"],
      ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false, RETURN_DOM_FRAGMENT: true,
    });
    markTechnicalText(fragment);
    container.current?.replaceChildren(fragment);
  }, [body, history, linkWikiNotes]);
  return <div className="markdown-body" ref={container} lang={lang} translate="yes" />;
}
