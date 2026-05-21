import type { Handle, HandleServerError } from "@sveltejs/kit";

const API_TARGET = process.env.API_TARGET ?? "http://localhost:8001";

const PROXY_PREFIXES = ["/api/", "/_allauth/", "/admin/", "/media/"];

function buildProxyHeaders(
  clientRequest: Request,
  clientUrl: URL,
): Headers {
  const headers = new Headers(clientRequest.headers);
  const proto = clientUrl.protocol.replace(":", "");
  headers.set("X-Forwarded-Host", clientUrl.host);
  headers.set("X-Forwarded-Proto", proto);
  headers.set("X-Forwarded-For", "127.0.0.1");
  // Preserve the browser Host so Django does not see the internal API_TARGET host.
  headers.set("Host", clientUrl.host);
  return headers;
}

export const handle: Handle = async ({ event, resolve }) => {
  const path = event.url.pathname;

  if (path === "/favicon.png" || path === "/favicon.ico") {
    return new Response(null, {
      status: 302,
      headers: { Location: "/favicon.svg" },
    });
  }

  if (PROXY_PREFIXES.some((prefix) => path.startsWith(prefix))) {
    const targetUrl = `${API_TARGET}${path}${event.url.search}`;
    const reqBody =
      event.request.method !== "GET" && event.request.method !== "HEAD"
        ? await event.request.arrayBuffer()
        : undefined;

    const upstream = await fetch(targetUrl, {
      method: event.request.method,
      headers: buildProxyHeaders(event.request, event.url),
      body: reqBody,
      // @ts-expect-error Node 18+ fetch supports duplex
      duplex: reqBody ? "half" : undefined,
    });

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: upstream.headers,
    });
  }

  return resolve(event);
};

export const handleError: HandleServerError = async ({ error, status }) => {
  const message = error instanceof Error ? error.message : String(error);
  // Missing static assets (favicon, old PWA paths) should not spam logs or mask real errors.
  if (
    status === 404 &&
    (/favicon\.(png|ico|svg)/i.test(message) ||
      /^Not found: \/(_app|sw\.js|favicon)/i.test(message))
  ) {
    return { message: "Not found" };
  }
  console.error("Server error:", error);
  return {
    message: "An unexpected error occurred",
  };
};
