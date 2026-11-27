import { layout, type LayoutInput } from "./graphLayout";

// Only topology and numeric indexes cross this boundary. Node text stays on the UI thread.
self.onmessage = ({data}: MessageEvent<LayoutInput>) => {
  const positions = layout(data);
  self.postMessage(positions, { transfer: [positions.buffer] });
};
