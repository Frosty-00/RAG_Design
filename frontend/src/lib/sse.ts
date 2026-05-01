/** SSE consumer for `text/event-stream` over fetch+ReadableStream.
 *
 *  We can't use the browser's native EventSource because it doesn't support
 *  POST or custom Authorization headers.
 *
 *  Yields `{event, data}` records as they arrive; consumer parses `data` as
 *  JSON if it expects structured events.
 */

export interface RawSSEEvent {
  event: string;       // event: <name>
  data: string;        // data: <payload> (may be JSON)
}

export async function* readSSE(
  resp: Response,
  signal?: AbortSignal,
): AsyncIterableIterator<RawSSEEvent> {
  if (!resp.body) throw new Error("response has no body");
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (signal?.aborted) {
        await reader.cancel();
        return;
      }
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Each event ends in a blank line (\n\n)
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const ev = parseEventBlock(raw);
        if (ev) yield ev;
      }
    }
  } finally {
    try { reader.releaseLock(); } catch { /* ignore */ }
  }
}

function parseEventBlock(block: string): RawSSEEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}
