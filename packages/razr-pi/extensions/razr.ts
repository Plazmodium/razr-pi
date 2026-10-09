import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const run = promisify(execFile);
const packageRoot = fileURLToPath(new URL("..", import.meta.url));
const python = process.env.RAZR_PI_PYTHON ?? "python3";

async function invoke(script: string, args: string[], cwd: string): Promise<string> {
  const result = await run(python, ["-I", "-B", `${packageRoot}/${script}`, ...args], {
    cwd,
    maxBuffer: 1024 * 1024,
    env: {
      PATH: process.env.PATH ?? "/usr/bin:/bin",
      LANG: "C.UTF-8",
      LC_ALL: "C.UTF-8",
      PYTHONIOENCODING: "UTF-8",
      GIT_CONFIG_NOSYSTEM: "1",
      GIT_CONFIG_GLOBAL: "/dev/null",
      GIT_OPTIONAL_LOCKS: "0",
      GIT_TERMINAL_PROMPT: "0",
    },
  });
  return result.stdout.trim();
}

function show(ctx: { ui: { notify(message: string, level: "info" | "error"): void } }, text: string) {
  ctx.ui.notify(text.length > 12000 ? `${text.slice(0, 12000)}\n[output truncated]` : text, "info");
}

function fail(ctx: { ui: { notify(message: string, level: "info" | "error"): void } }, error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  ctx.ui.notify(`Razr refused the request: ${message}`, "error");
}

export default function razrExtension(pi: ExtensionAPI) {
  pi.registerCommand("razr-status", {
    description: "Read the current Razr workflow status (read-only)",
    handler: async (args, ctx) => {
      try { show(ctx, await invoke("scripts/pi-surface.py", ["status", ...(args.trim() ? [args.trim()] : [])], ctx.cwd)); }
      catch (error) { fail(ctx, error); }
    },
  });

  pi.registerCommand("razr-context", {
    description: "Read a bounded Razr context snapshot (read-only)",
    handler: async (args, ctx) => {
      try { show(ctx, await invoke("scripts/pi-surface.py", ["context", args.trim()], ctx.cwd)); }
      catch (error) { fail(ctx, error); }
    },
  });

  pi.registerCommand("razr-remember", {
    description: "Append one explicit repository-local Razr memory observation",
    handler: async (args, ctx) => {
      try { show(ctx, await invoke("scripts/pi-surface.py", ["remember", args.trim()], ctx.cwd)); }
      catch (error) { fail(ctx, error); }
    },
  });

  pi.registerCommand("razr-recall", {
    description: "Recall repository-local Razr memory (read-only)",
    handler: async (args, ctx) => {
      try { show(ctx, await invoke("scripts/pi-surface.py", ["recall", args.trim()], ctx.cwd)); }
      catch (error) { fail(ctx, error); }
    },
  });

  pi.registerCommand("razr-memory-inspect", {
    description: "Inspect repository-local Razr memory (read-only)",
    handler: async (_args, ctx) => {
      try { show(ctx, await invoke("scripts/pi-surface.py", ["inspect"], ctx.cwd)); }
      catch (error) { fail(ctx, error); }
    },
  });
}
