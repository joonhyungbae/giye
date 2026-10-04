import { defineConfig, type PluginOption } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { nitro } from "nitro/vite";
import tsConfigPaths from "vite-tsconfig-paths";

// Production builds a self-hosted Node server via Nitro (the site reads data/site/*.json from disk and appends
// self-reports to data/work/requests.jsonl, so it needs a filesystem). The dev server keeps the
// previous host and port; web.sh may override both on the command line.
// GIYE_WATCH_POLL is set by web.sh when inotify instances are exhausted.
export default defineConfig(({ command }) => {
  const plugins: PluginOption[] = [
    tsConfigPaths({ projects: ["./tsconfig.json"] }),
    tailwindcss(),
    tanstackStart({
      importProtection: {
        behavior: "error",
        client: {
          files: ["**/server/**"],
          specifiers: ["server-only"],
        },
      },
      server: { entry: "server" },
    }),
    viteReact(),
  ];
  if (command === "build") {
    plugins.push(nitro({ defaultPreset: "node-server" }));
  }

  return {
    plugins,
    resolve: {
      alias: { "@": `${process.cwd()}/src` },
      dedupe: [
        "react",
        "react-dom",
        "react/jsx-runtime",
        "react/jsx-dev-runtime",
        "@tanstack/react-query",
        "@tanstack/query-core",
      ],
    },
    optimizeDeps: {
      include: ["three", "@react-three/fiber"],
    },
    server: {
      host: "::",
      port: 8080,
      watch: process.env.GIYE_WATCH_POLL
        ? {
            usePolling: true,
            interval: 400,
            awaitWriteFinish: { stabilityThreshold: 1000, pollInterval: 100 },
          }
        : { awaitWriteFinish: { stabilityThreshold: 1000, pollInterval: 100 } },
    },
  };
});
