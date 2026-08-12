const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "public, max-age=60, s-maxage=300",
};

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: JSON_HEADERS,
  });
}

async function snapshot(env, request) {
  const assetUrl = new URL("/research_result.json", request.url);
  const response = await env.ASSETS.fetch(new Request(assetUrl, request));
  if (!response.ok) {
    return json({ error: "The deployed research snapshot is missing." }, 500);
  }
  const result = await response.json();
  return json({
    ...result,
    deployment: {
      platform: "Cloudflare Workers Static Assets",
      static_snapshot: true,
      generated_at: result.provenance?.retrieved_at ?? null,
      requested_query: Object.fromEntries(new URL(request.url).searchParams.entries()),
      note: "The edge deployment serves a reproducible precomputed snapshot. Run the Python research engine and redeploy to refresh it.",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/health") {
      return json({
        status: "ok",
        deployment: "cloudflare-static-snapshot",
        research_window: "30Y",
        optimizer: "robust_quant",
      });
    }

    if (url.pathname === "/api/research") {
      return snapshot(env, request);
    }

    return env.ASSETS.fetch(request);
  },
};
