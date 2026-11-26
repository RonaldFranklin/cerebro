import { createRoot } from "react-dom/client";
import { Markdown, markdownHeadings, TechnicalText } from "../../../src/Markdown";

const body = [
  "# First section", "Ordinary prose and **strong text**. Read [[valid-note#Setup]] or [[redacted link]].",
  "Run git status in ./notes/demo.md and use `uv run pytest`. Keep note-id unchanged.", "",
  "Follow the step-by-step instructions for docker compose up -d and read [[missing-note]].", "",
  `[Other port](https://${location.hostname}:5483/) and [dotted host](https://${location.hostname}.:5483/).`, "",
  "| Name | Value |", "| --- | --- |", "| Example | One |", "",
  'Raw <em title="fixture">markup</em> remains text.', "",
  '![Image](https://example.invalid/fixture.png)', "",
  '[Website](https://example.invalid/guide) and [invalid URL](data:text/plain,fixture).', "",
  '[network path](//example.invalid/fixture) and [internal redirect](/api/example).', "",
  '<video src="https://example.invalid/fixture.webm"></video>', "",
  "~~~text", "## Code heading", "[[example-code]]", "~~~", "", "## Second section",
].join("\n");
const root = createRoot(document.getElementById("root")!);
const visibleIds = new Set(["valid-note", "note-id"]);
root.render(<>
  <Markdown body={body} lang="en" visibleIds={location.search ? undefined : visibleIds} history />
  <output>{JSON.stringify(markdownHeadings(body))}</output>
  <h2 className="plain-fixture"><TechnicalText text="Use note-id and docker compose up -d step-by-step; *literal*." visibleIds={visibleIds} /></h2>
</>);
