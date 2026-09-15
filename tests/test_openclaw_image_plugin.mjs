import assert from "node:assert/strict";
import plugin from "../openclaw-plugin/index.js";

const registrations = [];
plugin.register({ registerImageGenerationProvider: (value) => registrations.push(value) });
assert.equal(registrations.length, 1);
const provider = registrations[0];
assert.equal(provider.id, "wmadapter");
assert.deepEqual(provider.models, ["qwen-chat"]);
await assert.rejects(
  provider.generateImage({ model: "deepseek-chat", prompt: "x", cfg: {} }),
  /only supported for qwen-chat/
);
await assert.rejects(
  provider.generateImage({ model: "qwen-chat", prompt: "x", size: "1024x1024", cfg: {} }),
  /does not support the image option: size/
);
await assert.rejects(
  provider.generateImage({ model: "qwen-chat", prompt: "x", count: 2, cfg: {} }),
  /supports only count=1/
);

const originalFetch = globalThis.fetch;
let request;
globalThis.fetch = async (url, options) => {
  request = { url, options };
  return new Response(JSON.stringify({ data: [{ b64_json: "aGVsbG8=", mime_type: "image/jpeg" }] }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};
try {
  const result = await provider.generateImage({
    model: "qwen-chat",
    prompt: "  a cat  ",
    count: 1,
    cfg: { models: { providers: { wmadapter: { baseUrl: "http://gateway.test/v1", apiKey: "secret" } } } },
  });
  assert.equal(request.url, "http://gateway.test/v1/images");
  assert.equal(request.options.headers.authorization, "Bearer secret");
  assert.deepEqual(JSON.parse(request.options.body), {
    model: "qwen-chat", prompt: "  a cat  ", n: 1, response_format: "b64_json",
  });
  assert.equal(result.images[0].mimeType, "image/jpeg");
  assert.equal(result.images[0].buffer.toString(), "hello");
} finally {
  globalThis.fetch = originalFetch;
}
console.log("OpenClaw Wmadapter image provider fixture passed");
