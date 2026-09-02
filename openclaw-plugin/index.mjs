import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const bridgeUrl = process.env.WEBBRIDGE_URL || "http://127.0.0.1:11556";

export default definePluginEntry({
  id: "webbridgefreeride-openclaw",
  name: "WebBridge FreeRide cleanup",
  description: "Release WebBridge browser sessions when OpenClaw sessions are deleted.",
  register(api) {
    api.on("session_end", async (event, ctx) => {
      if (event.reason !== "deleted" || !ctx.sessionKey) return;
      const id = encodeURIComponent(ctx.sessionKey);
      try {
        const response = await fetch(`${bridgeUrl}/v1/conversations/${id}?model=deepseek-chat`, {
          method: "DELETE",
          signal: AbortSignal.timeout(1500),
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
