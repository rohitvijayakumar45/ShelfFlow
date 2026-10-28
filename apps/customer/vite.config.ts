import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";
// `vite --mode lan`: serve on the hotspot over HTTPS (phones only open the camera on https pages),
// with the certificate from `python -m tools.demo_lan`.
const certs = path.resolve(__dirname, "../../data/demo-certs");
const lan = (mode: string) =>
  mode === "lan"
    ? { host: true, https: { key: fs.readFileSync(path.join(certs, "server.key")), cert: fs.readFileSync(path.join(certs, "server.pem")) } }
    : {};
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    ...lan(mode),
    fs: { allow: [".."] },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
}));
