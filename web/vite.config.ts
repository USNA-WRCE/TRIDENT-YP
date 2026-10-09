import { existsSync, createReadStream, statSync } from "node:fs";
import { extname, join, normalize } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const OPENMCT_DIST = join(__dirname, "node_modules", "openmct", "dist");
const MIME: Record<string, string> = {
  ".js": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".json": "application/json",
  ".map": "application/json",
};

// The Docker image copies OpenMCT to /vendor/openmct; mirror that in dev.
function serveOpenMct(): Plugin {
  return {
    name: "serve-openmct",
    configureServer(server) {
      server.middlewares.use("/vendor/openmct", (req, res, next) => {
        const relative = normalize(decodeURIComponent((req.url ?? "/").split("?")[0])).replace(/^([/\\])+/, "");
        const file = join(OPENMCT_DIST, relative);
        if (!file.startsWith(OPENMCT_DIST) || !existsSync(file) || !statSync(file).isFile()) {
          next();
          return;
        }
        res.setHeader("Content-Type", MIME[extname(file)] ?? "application/octet-stream");
        createReadStream(file).pipe(res);
      });
    },
  };
}

export default defineConfig({
  base: process.env.GITHUB_PAGES === "true" ? "/yp_ground_station/" : "/",
  plugins: [react(), serveOpenMct()],
  // Preserve dynamic-import boundaries. Grouping React/Three manually pulls
  // shared React helpers into the 3D chunk and makes the map preload it.
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/tiles": "http://localhost:8000",
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
});
