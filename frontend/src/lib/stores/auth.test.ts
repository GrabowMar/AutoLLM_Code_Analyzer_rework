import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

async function signup(response: Response) {
  vi.stubGlobal("document", { cookie: "csrftoken=test-token" });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  const { getAuth } = await import("./auth.svelte");
  return getAuth().signup("signup@example.org", "password", "password");
}

it("explains when the server has closed registration", async () => {
  const result = await signup(Response.json({ status: 403 }, { status: 403 }));
  expect(result).toEqual({
    ok: false,
    error: "Registration is currently closed. Contact the site administrator.",
  });
});

it("does not describe a CSRF rejection as closed registration", async () => {
  const result = await signup(
    new Response("CSRF verification failed", { status: 403 }),
  );
  expect(result).toEqual({ ok: false, error: "Sign up failed (HTTP 403)." });
});

it("accepts signup awaiting email confirmation", async () => {
  const result = await signup(
    Response.json(
      {
        status: 401,
        data: { flows: [{ id: "verify_email", is_pending: true }] },
      },
      { status: 401 },
    ),
  );
  expect(result).toEqual({ ok: true, pendingFlow: "verify_email" });
});
