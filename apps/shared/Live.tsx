import { ReactNode, useEffect, useRef, useState } from "react";

/** Live MJPEG through fetch (keeps the staff token out of URLs); draws only the newest frame, so a slow
 *  screen skips frames instead of falling behind. `url` is the full path, e.g. "/api/staff/camera/stream". */
export function MjpegView({ url, token, children, onFps, empty }: {
  url: string; token: string; children?: ReactNode; onFps?: (fps: number) => void; empty?: ReactNode;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [live, setLive] = useState(false);
  const fpsRef = useRef(onFps);
  fpsRef.current = onFps;
  useEffect(() => {
    const ctrl = new AbortController();
    let stopped = false,
      retry: ReturnType<typeof setTimeout>,
      isLive = false;
    const shown: number[] = [];
    const mark = (v: boolean) => {
      if (v !== isLive) setLive((isLive = v));
      if (!v) fpsRef.current?.(0);
    };
    async function run() {
      try {
        const r = await fetch(url, { headers: { Authorization: `Bearer ${token}` }, signal: ctrl.signal, cache: "no-store" });
        if (!r.ok || !r.body) throw new Error(String(r.status));
        const reader = r.body.getReader();
        const decoder = new TextDecoder();
        let buf = new Uint8Array(0),
          drawing = false;
        while (!stopped) {
          const { value, done } = await reader.read();
          if (done) break;
          const next = new Uint8Array(buf.length + value.length);
          next.set(buf);
          next.set(value, buf.length);
          buf = next;
          let latest: Uint8Array | null = null;
          for (;;) {
            let h = -1;
            for (let i = 0; i + 3 < buf.length; i++)
              if (buf[i] === 13 && buf[i + 1] === 10 && buf[i + 2] === 13 && buf[i + 3] === 10) {
                h = i;
                break;
              }
            if (h < 0) break;
            const m = /content-length:\s*(\d+)/i.exec(decoder.decode(buf.subarray(0, h)));
            if (!m) {
              buf = buf.slice(h + 4);
              continue;
            }
            const end = h + 4 + Number(m[1]);
            if (buf.length < end) break;
            latest = buf.slice(h + 4, end);
            buf = buf.slice(end);
          }
          if (latest && !drawing) {
            drawing = true;
            createImageBitmap(new Blob([latest as BlobPart], { type: "image/jpeg" }))
              .then((bmp) => {
                const c = canvas.current;
                if (c) {
                  if (c.width !== bmp.width || c.height !== bmp.height) {
                    c.width = bmp.width;
                    c.height = bmp.height;
                  }
                  c.getContext("2d")!.drawImage(bmp, 0, 0);
                  mark(true);
                  const now = performance.now();
                  shown.push(now);
                  while (shown.length && now - shown[0] > 2000) shown.shift();
                  if (shown.length > 2) fpsRef.current?.(Math.round(((shown.length - 1) * 1000) / (now - shown[0])));
                }
                bmp.close();
              })
              .catch(() => {})
              .finally(() => (drawing = false));
          }
        }
        throw new Error("ended");
      } catch {
        if (!stopped) {
          mark(false);
          retry = setTimeout(run, 2000);
        }
      }
    }
    run();
    return () => {
      stopped = true;
      ctrl.abort();
      clearTimeout(retry);
    };
  }, [url, token]);
  return (
    <div className="livebox">
      <canvas ref={canvas} width={640} height={480} aria-label="Live camera" />
      {!live && <div className="liveempty">{empty || "Waiting for camera frames…"}</div>}
      {children}
    </div>
  );
}
