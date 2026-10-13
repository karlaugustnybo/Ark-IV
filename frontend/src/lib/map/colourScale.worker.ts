import { jenksBreaks } from './colourScales';

self.onmessage = (event: MessageEvent<{ id: number; scores: Float64Array; classes: number }>) => {
  const { id, scores, classes } = event.data;
  self.postMessage({ id, breaks: jenksBreaks(scores, classes) });
};
