import type { Handle, HandleServerError, RequestEvent } from "@sveltejs/kit";

const API_TARGET = process.env.API_TARGET ?? "http://localhost:8001";

const PROXY_PREFIXES = ["/api/", "/_allauth/", "/admin/", "/media/", "/apps/"];

// Node fetch transparently decompresses the upstream body, so these headers
// would describe bytes the client never receives (a stale gzip header breaks
// every client that honours it, e.g. the relay's own gunzip).
const STRIPPED_RESPONSE_HEADERS = ["content-encoding", "content-length"];

function clientAddress(event: RequestEvent): string | null {
  try {
    return event.getClientAddress();
  } catch {
    return null;
  }
}

export function buildProxyHeaders(
  clientRequest: Request,
  clientUrl: URL,
  clientIp: string | null,
): Headers {
  const headers = new Headers(clientRequest.headers);
  const proto = clientUrl.protocol.replace(":", "");
  headers.set("X-Forwarded-Host", clientUrl.host);
  headers.set("X-Forwarded-Proto", proto);
  // Append our peer to the chain (Caddy's hops stay on the left); Django
  // trusts only the hops added by TRUSTED_PROXY_CIDRS.
  const incoming = clientRequest.headers.get("X-Forwarded-For");
  const hops = [incoming, clientIp].filter((h): h is string =>
    Boolean(h && h.trim()),
  );
  if (hops.length) headers.set("X-Forwarded-For", hops.join(", "));
  else headers.delete("X-Forwarded-For");
  // Preserve the browser Host so Django does not see the internal API_TARGET host.
  headers.set("Host", clientUrl.host);
  return headers;
}

export function proxyResponseHeaders(upstream: Headers): Headers {
  const headers = new Headers(upstream);
  for (const name of STRIPPED_RESPONSE_HEADERS) headers.delete(name);
  return headers;
}

export async function proxyToApi(event: RequestEvent): Promise<Response> {
  const path = event.url.pathname;
  const targetUrl = `${API_TARGET}${path}${event.url.search}`;
  const reqBody =
    event.request.method !== "GET" && event.request.method !== "HEAD"
      ? await event.request.arrayBuffer()
      : undefined;

  let upstream: Response;
  try {
    upstream = await fetch(targetUrl, {
      method: event.request.method,
      headers: buildProxyHeaders(
        event.request,
        event.url,
        clientAddress(event),
      ),
      body: reqBody,
      // A client that disconnects (browser tab, relay long-poll) cancels the Django request.
      signal: event.request.signal,
      // @ts-expect-error Node 18+ fetch supports duplex
      duplex: reqBody ? "half" : undefined,
    });
  } catch (error) {
    // The client went away: nobody reads this response, and a routine
    // disconnect must not be logged as a server error.
    if (event.request.signal.aborted) {
      return new Response(null, { status: 499 });
    }
    throw error;
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: proxyResponseHeaders(upstream.headers),
  });
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
    return proxyToApi(event);
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
