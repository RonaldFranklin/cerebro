import { markdown } from "@codemirror/lang-markdown";
import { EditorView } from "@codemirror/view";

new EditorView({
  doc: "Toolchain test",
  extensions: [markdown()],
  parent: document.querySelector("main")!,
});
