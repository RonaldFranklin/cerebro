import { createRoot } from "react-dom/client";
import { Markdown, markdownHeadings } from "../../../src/Markdown";

const body = [
  "# First section", "Ordinary prose and **strong text**. Read [[valid-note#Setup]] or [[redacted link]].",
  "Run git status in ./notes/demo.md and use `uv run pytest`. Keep note-id unchanged.", "",
  "| Name | Value |", "| --- | --- |", "| Example | One |", "",
  'Raw <em title="fixture">markup</em> remains text.', "",
  '![Image](https://example.invalid/fixture.png)', "",
  '[Website](https://example.invalid/guide) and [invalid URL](data:text/plain,fixture).', "",
  '[network path](//example.invalid/fixture) and [internal redirect](/api/example).', "",
  '<video src="https://example.invalid/fixture.webm"></video>', "",
  "~~~text", "## Code heading", "[[example-code]]", "~~~", "", "## Second section",
].join("\n");
const root = createRoot(document.getElementById("root")!);
root.render(<><Markdown body={body} lang="en" history /><output>{JSON.stringify(markdownHeadings(body))}</output></>);
