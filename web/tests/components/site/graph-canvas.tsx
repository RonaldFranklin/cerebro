import { useState } from "react";
import { createRoot } from "react-dom/client";
import { GraphCanvas } from "../../../src/GraphCanvas";
import type { GraphData } from "../../../src/visibleGraph";

const data: GraphData = {
  nodes: [{id:"fixture",title:"Fixture",summary:"Example",area:"research",type:"guide",priority:"P1",status:"current",sensitivity:"public"}],
  edges: [], center: null,
};
function Fixture() {
  const [selected, setSelected] = useState("");
  return <><GraphCanvas data={data} colorBy="area" colorOf={() => "#5ce1e6"} selected={selected} active={new Set()} onSelect={setSelected}/><output>{selected}</output></>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
