/**
 * edgar-proxy — Cloudflare Worker
 *
 * Forwards requests to sec.gov using Cloudflare IPs so GitHub Actions
 * (which runs on Azure IPs that SEC EDGAR blocks) can still fetch data.
 *
 * Usage:  GET https://<your-worker>.workers.dev?url=https://data.sec.gov/...
 *
 * Deploy: Cloudflare Dashboard → Workers & Pages → Create → Worker
 *         Paste this script, click Save & Deploy.
 *         Copy the worker URL, add it as EDGAR_PROXY in your GitHub repo variables.
 */

const SEC_USER_AGENT = "willmyfundbuyspacex ebraheemdababo@gmail.com";

export default {
  async fetch(request) {
    const { searchParams } = new URL(request.url);
    const target = searchParams.get("url");

    // Require a target URL
    if (!target) {
      return new Response("Missing ?url= parameter", { status: 400 });
    }

    // Security: only proxy .sec.gov URLs
    let targetUrl;
    try {
      targetUrl = new URL(target);
    } catch {
      return new Response("Invalid URL", { status: 400 });
    }
    if (!targetUrl.hostname.endsWith(".sec.gov")) {
      return new Response("Only .sec.gov URLs are allowed", { status: 403 });
    }

    // Fetch from SEC with required headers
    const resp = await fetch(target, {
      headers: {
        "User-Agent": SEC_USER_AGENT,
        // Ask for plain (not compressed) so Cloudflare doesn't re-encode
        "Accept-Encoding": "identity",
        "Accept": "application/json, text/html, application/xml, */*",
      },
      redirect: "follow",
    });

    // Return the body as-is, without re-encoding
    return new Response(resp.body, {
      status: resp.status,
      headers: {
        "Content-Type": resp.headers.get("Content-Type") || "application/octet-stream",
        // No Content-Encoding header — body is already plain text
        "Cache-Control": "max-age=300",
        "Access-Control-Allow-Origin": "*",
      },
    });
  },
};
