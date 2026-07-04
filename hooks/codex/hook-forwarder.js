const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const APP_DIR = __dirname;
const PORT = Number(process.env.AGENT_SIGNAL_LIGHT_PORT || 8787);
const BASE_URL = `http://127.0.0.1:${PORT}`;
const SERVER_URL = `${BASE_URL}/hook`;
const LOG_PATH = path.join(APP_DIR, "hook.log");
const WORKSPACE_ROOT = path.resolve(APP_DIR, "..");
const BRIDGE_SCRIPT_PATH = path.join(WORKSPACE_ROOT, "codex_status_bridge.py");

function pythonCommand() {
  const venvPython = path.join(WORKSPACE_ROOT, ".venv", "bin", "python");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return process.platform === "win32" ? "python" : (process.env.PYTHON || "python3");
}

function bridgeStarterCommand() {
  const bridgeStarter = path.join(WORKSPACE_ROOT, "start-bridge-mac.sh");
  if (process.platform !== "win32" && fs.existsSync(bridgeStarter)) {
    return { command: "sh", args: [bridgeStarter] };
  }
  return {
    command: pythonCommand(),
    args: ["-u", BRIDGE_SCRIPT_PATH]
  };
}

function log(message) {
  const line = `${new Date().toISOString()} ${message}\n`;
  try {
    fs.appendFileSync(LOG_PATH, line, "utf8");
  } catch {}
}

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on("data", (chunk) => chunks.push(chunk));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
}

async function postHook(agent, raw) {
  const response = await fetch(`${SERVER_URL}?agent=${encodeURIComponent(agent)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: raw
  });
  if (!response.ok) {
    throw new Error(`hook post failed: ${response.status}`);
  }
}

function startServerDetached() {
  const child = spawn("node", ["server.js"], {
    cwd: APP_DIR,
    detached: true,
    windowsHide: true,
    stdio: "ignore",
    env: { ...process.env, PORT: String(PORT) }
  });
  child.unref();
}

function startBridgeDetached() {
  if (!fs.existsSync(BRIDGE_SCRIPT_PATH)) {
    log(`bridge script missing: ${BRIDGE_SCRIPT_PATH}`);
    return;
  }

  try {
    const { execSync } = require("node:child_process");
    execSync("pgrep -f 'codex_status_bridge.py|ble_status_bridge.py'", { stdio: "ignore" });
    return;
  } catch {
    // Bridge not running yet.
  }

  const starter = bridgeStarterCommand();
  const child = spawn(starter.command, starter.args, {
    cwd: WORKSPACE_ROOT,
    detached: true,
    windowsHide: true,
    stdio: "ignore",
    env: {
      ...process.env,
      http_proxy: "",
      https_proxy: "",
      HTTP_PROXY: "",
      HTTPS_PROXY: "",
      ALL_PROXY: "",
      all_proxy: ""
    }
  });
  child.unref();
}

async function ensureServer(agent, raw) {
  try {
    await postHook(agent, raw);
    startBridgeDetached();
    return "posted";
  } catch (error) {
    log(`first post failed: ${error.message}`);
  }

  startServerDetached();
  startBridgeDetached();
  await new Promise((resolve) => setTimeout(resolve, 1200));
  await postHook(agent, raw);
  return "started-and-posted";
}

async function main() {
  const agent = String(process.argv[2] || "unknown").toLowerCase();
  const raw = await readStdin();
  if (!raw.trim()) {
    log(`empty payload agent=${agent}`);
    return;
  }

  try {
    const result = await ensureServer(agent, raw);
    let eventName = "unknown";
    try {
      const parsed = JSON.parse(raw);
      eventName = parsed.hook_event_name || parsed.event || parsed.event_name || "unknown";
    } catch {}
    log(`agent=${agent} event=${eventName} result=${result}`);
  } catch (error) {
    log(`agent=${agent} error=${error.message}`);
  }
}

main().catch((error) => {
  log(`fatal=${error.message}`);
  process.exit(0);
});
