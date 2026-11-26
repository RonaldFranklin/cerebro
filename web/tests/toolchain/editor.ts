import { markdown } from "@codemirror/lang-markdown";
import { EditorView } from "@codemirror/view";

const parent = document.querySelector("main")!;
new EditorView({
  doc: "Toolchain test",
  extensions: [markdown(), EditorView.cspNonce.of(parent.nonce)],
  parent,
});
