// One tool-less GitHub Copilot turn for the BytecodeAgent live eval (bca/liveeval/vendor_copilot.py).
//
// Usage: node copilot_driver.mjs <config.json>
//   config: {sdk, cli, home, work?, model, effort?, system, prompt, cliArgs}   (work: default the cwd)
//
// Drives the Copilot CLI runtime (`copilot --headless --stdio`, spawned by the Copilot SDK) with a session
// whose system prompt is replaced by ours, every tool source excluded, no MCP server, no custom
// instructions / agents / skills / memory, and every permission request rejected. Every session event is
// written to stdout as one JSON line; the adapter checks them (and the runtime's own log of the request).
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const work = cfg.work || process.cwd();
const out = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
const { CopilotClient, RuntimeConnection } = await import(pathToFileURL(cfg.sdk).href);

const client = new CopilotClient({
  mode: "empty", // no ambient features: every one this session needs is named below
  baseDirectory: cfg.home, // COPILOT_HOME: the private home
  workingDirectory: work,
  logLevel: "all", // the runtime logs the wire request, which the adapter verifies
  useLoggedInUser: true, // headless: the GitHub CLI's login (`gh auth token`)
  connection: RuntimeConnection.forStdio({ path: cfg.cli, args: cfg.cliArgs || [], env: process.env }),
});
let exitCode = 0;
try {
  await client.start();
  out({ type: "bca.auth", data: await client.getAuthStatus() });
  const session = await client.createSession({
    model: cfg.model,
    ...(cfg.effort ? { reasoningEffort: cfg.effort } : {}),
    streaming: true,
    systemMessage: { mode: "replace", content: cfg.system },
    availableTools: [],
    excludedTools: ["builtin:*", "mcp:*", "custom:*"],
    mcpServers: {},
    disabledMcpServers: ["github-mcp-server"],
    customAgents: [],
    skillDirectories: [],
    instructionDirectories: [],
    skipCustomInstructions: true,
    infiniteSessions: { enabled: false },
    memory: { enabled: false },
    coauthorEnabled: false,
    manageScheduleEnabled: false,
    workingDirectory: work,
    onPermissionRequest: (req) => {
      out({ type: "bca.permission_request", data: req });
      return { kind: "reject" };
    },
    hooks: {
      // The runtime prefixes the prompt (a <current_datetime> block); the subject gets the prompt as given.
      onUserPromptTransformed: (input) => {
        out({ type: "bca.prompt_transformed",
              data: { changed: input.transformedPrompt !== input.prompt, transformed: input.transformedPrompt.slice(0, 400) } });
        return { modifiedTransformedPrompt: input.prompt };
      },
    },
  });
  out({ type: "bca.session", data: { sessionId: session.sessionId } });
  const idle = new Promise((resolve) => {
    session.on((ev) => {
      out(ev);
      if (ev.type === "session.idle") resolve();
    });
  });
  await session.send({ prompt: cfg.prompt });
  await idle;
  await session.disconnect?.();
} catch (e) {
  out({ type: "bca.error", data: { message: String((e && e.message) || e) } });
  exitCode = 1;
} finally {
  try {
    await client.stop();
  } catch (e) {
    out({ type: "bca.stop_error", data: { message: String(e) } });
  }
}
process.exit(exitCode);
