const SUPPORTED_MODEL = "qwen-chat";
const UNSUPPORTED_OPTIONS = new Set([
  "size", "aspectRatio", "resolution", "quality", "outputFormat",
  "background", "inputImages", "providerOptions"
]);

function configuredBaseUrl(cfg) {
  const configured = cfg?.models?.providers?.wmadapter?.baseUrl;
  return (typeof configured === "string" && configured.trim()
    ? configured : "http://127.0.0.1:11555/v1").replace(/\/$/, "");
}

function compatibilityError(message, code = "wmadapter_image_unsupported") {
  const error = new Error(message);
  error.code = code;
  return error;
}

function provider() {
  return {
    id: "wmadapter",
    aliases: ["wmadapter-image"],
    label: "Wmadapter",
    defaultModel: SUPPORTED_MODEL,
    models: [SUPPORTED_MODEL],
    capabilities: {
      generate: { maxCount: 1, supportsSize: false, supportsAspectRatio: false, supportsResolution: false },
      edit: { enabled: false },
    },
    isConfigured: ({ cfg } = {}) => Boolean(cfg?.models?.providers?.wmadapter?.baseUrl),
    async generateImage(req) {
      if (req.model !== SUPPORTED_MODEL) {
        throw compatibilityError(`Wmadapter image generation is only supported for ${SUPPORTED_MODEL}; received ${req.model}`);
      }
      for (const key of UNSUPPORTED_OPTIONS) {
        if (req[key] !== undefined) {
          throw compatibilityError(`Wmadapter /v1/images does not support the image option: ${key}`);
        }
      }
      if (req.count !== undefined && req.count !== 1) {
        throw compatibilityError("Wmadapter image generation supports only count=1");
      }
      if (typeof req.prompt !== "string" || !req.prompt.trim()) {
        throw compatibilityError("Wmadapter image generation requires a non-empty prompt", "wmadapter_image_invalid_prompt");
      }
      const apiKey = req.cfg?.models?.providers?.wmadapter?.apiKey;
      const headers = { "content-type": "application/json" };
      if (typeof apiKey === "string" && apiKey.trim()) {
        headers.authorization = `Bearer ${apiKey.trim()}`;
      }
      const response = await fetch(`${configuredBaseUrl(req.cfg)}/images`, {
        method: "POST",
        headers,
        body: JSON.stringify({ model: req.model, prompt: req.prompt, n: 1, response_format: "b64_json" }),
        signal: AbortSignal.timeout(req.timeoutMs ?? 180000),
      });
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const body = await response.json();
          detail = body?.detail || body?.error?.message || detail;
        } catch {}
        throw compatibilityError(`Wmadapter image generation failed: ${detail}`, `wmadapter_image_http_${response.status}`);
      }
      const body = await response.json();
      const images = Array.isArray(body.data) ? body.data : [];
      if (!images.length) throw compatibilityError("Wmadapter returned no generated image data", "wmadapter_image_empty_response");
      return { images: images.map((image) => {
        if (typeof image?.b64_json !== "string" || !image.b64_json) {
          throw compatibilityError("Wmadapter returned invalid image data", "wmadapter_image_invalid_response");
        }
        return {
          buffer: Buffer.from(image.b64_json, "base64"),
          mimeType: typeof image.mime_type === "string" && image.mime_type ? image.mime_type : "image/png",
        };
      }) };
    },
  };
}

export default { id: "wmadapter-image", register(api) { api.registerImageGenerationProvider(provider()); } };
