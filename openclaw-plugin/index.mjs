import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const bridgeUrl = process.env.WEBBRIDGE_URL || "http://127.0.0.1:11556";
const execFileAsync = promisify(execFile);
const openclawNode = "/usr/local/bin/node";
const openclawCli = "/home/arash/.npm-global/lib/node_modules/openclaw/openclaw.mjs";

async function browserCli(api, args) {
  const token = api.config?.gateway?.auth?.token;
  const env = { ...process.env, ...(typeof token === "string" ? { OPENCLAW_GATEWAY_TOKEN: token } : {}) };
  const result = await execFileAsync(openclawNode, [openclawCli, "browser", ...args, "--json"], {
    env,
    timeout: 30000,
    maxBuffer: 2 * 1024 * 1024,
  });
  return JSON.parse(result.stdout);
}

async function deleteInConnectedChrome(api, url) {
  if (!url) return false;
  const request = async (method, path, body) => {
    try {
      return await api.runtime.gateway.request("browser.request", {
        method,
        path,
        query: { profile: "chrome" },
        ...(body ? { body } : {}),
      }, { timeoutMs: 30000, scopes: ["operator.admin"] });
    } catch (error) {
      if (method === "POST" && path === "/tabs/open") {
        return browserCli(api, ["open", body.url, "--browser-profile", "chrome"]);
      }
      if (method === "POST" && path === "/act" && body?.kind === "evaluate") {
        return browserCli(api, ["evaluate", "--browser-profile", "chrome", "--target-id", body.targetId, "--fn", body.fn]);
      }
      if (method === "DELETE" && path.startsWith("/tabs/")) {
        return browserCli(api, ["close", "--browser-profile", "chrome", path.slice("/tabs/".length)]);
      }
      throw error;
    }
  };

  const opened = await request("POST", "/tabs/open", { url });
  const targetId = opened?.targetId || opened?.tabId;
  if (!targetId) return false;
  const fn = `async () => {
    const visible = (el) => el && el.getBoundingClientRect().width > 0 && getComputedStyle(el).visibility !== "hidden";
    const exact = (text) => [...document.querySelectorAll("*")].filter((el) => visible(el) && el.textContent?.trim() === text).at(-1);
    const link = document.querySelector('a[href="${new URL(url).pathname}"]');
    link?.querySelector('[role="button"]')?.click();
    await new Promise((resolve) => setTimeout(resolve, 300));
    exact("Delete")?.click();
    await new Promise((resolve) => setTimeout(resolve, 300));
    const confirm = exact("Delete chat");
    if (!confirm) return false;
    setTimeout(() => confirm.click(), 0);
    return true;
  }`;
  const result = await request("POST", "/act", { kind: "evaluate", targetId, fn });
  await new Promise((resolve) => setTimeout(resolve, 1500));
  try {
    await request("DELETE", `/tabs/${encodeURIComponent(targetId)}`);
  } catch { /* DeepSeek navigates away after deletion. */ }
  return result?.result === true || result?.result?.value === true;
}

export default definePluginEntry({
  id: "webbridgefreeride-openclaw",
  name: "WebBridge FreeRide cleanup",
  description: "Delete matching DeepSeek Web conversations when OpenClaw sessions are deleted.",
  register(api) {
    api.on("session_start", async (event) => {
      if (!event.sessionId || !event.sessionKey) return;
      try {
        await fetch(`${bridgeUrl}/v1/conversations/bind?model=deepseek-chat`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ session_id: event.sessionId, session_key: event.sessionKey }),
          signal: AbortSignal.timeout(5000),
        });
      } catch (error) {
        api.logger.warn(`WebBridge session binding failed: ${error instanceof Error ? error.message : String(error)}`);
      }
    });

    api.on("session_end", async (event, ctx) => {
      if (event.reason !== "deleted") return;
      // OpenClaw sends sessionId to the model adapter; use it first so the
      // WebBridge page mapping is released even when the logical key differs.
      const conversationId = event.sessionId || ctx.sessionKey;
      if (!conversationId) return;
      const id = encodeURIComponent(conversationId);
      try {
        const details = await fetch(`${bridgeUrl}/v1/conversations/${id}?model=deepseek-chat`, {
          signal: AbortSignal.timeout(5000),
        });
        const payload = details.ok ? await details.json() : {};
        if (payload.url) {
          try {
            await deleteInConnectedChrome(api, payload.url);
          } catch (error) {
            api.logger.warn(`Connected Chrome cleanup unavailable: ${error instanceof Error ? error.message : String(error)}`);
          }
        }
        const response = await fetch(`${bridgeUrl}/v1/conversations/${id}?model=deepseek-chat`, {
          method: "DELETE",
          signal: AbortSignal.timeout(15000),
        });
        if (!response.ok) {
          api.logger.warn(`WebBridge cleanup returned HTTP ${response.status}`);
        }
      } catch (error) {
        api.logger.warn(`WebBridge cleanup failed: ${error instanceof Error ? error.message : String(error)}`);
      }
    });
  },
});
