import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
const root = path.dirname(fileURLToPath(import.meta.url));
for (const app of ["customer", "staff"]) {
  for (const args of [
    [
      path.join(root, "node_modules/typescript/bin/tsc"),
      "-p",
      path.join(root, app, "tsconfig.json"),
      "--noEmit",
    ],
    [path.join(root, "node_modules/vite/bin/vite.js"), "build"],
  ]) {
    const result = spawnSync(process.execPath, args, {
      cwd: path.join(root, app),
      stdio: "inherit",
    });
    if (result.status !== 0) process.exit(result.status || 1);
  }
}
