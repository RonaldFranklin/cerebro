# ADR-0009: English only, browser-translatable UI

- **Status:** accepted, amended by [ADR-0020](0020-direct-agent-writes.md) (no curator: writing agents submit English)
- **Date:** 2026-11-24

## Context

The project is public, and the owner may read in Portuguese. Maintaining translated UI strings and translated
notes doubles effort and cost.

## Decision

- Code, docs, UI text and note content are in English. No i18n library.
- The curator normalizes proposals to English.
- Users who need another language use the browser's built-in translation. The UI must support it:
  - notes rendered as real HTML text with correct `lang` attributes;
  - `translate="no"` on code blocks, inline code, commands, paths, note ids and wikilinks.
- Notes carry a `lang` field (default `en`) for future flexibility.

## Consequences

- Simpler UI and lower curator cost; English also uses fewer tokens per concept in most LLMs.
- Graph labels drawn on canvas (WebGL) are not translated by the browser; the reader panel is.

## Alternatives considered

- Bilingual UI (en/pt-BR) with i18n: extra maintenance for little gain.
- Bilingual notes: doubles storage and curation cost and creates sync problems.
