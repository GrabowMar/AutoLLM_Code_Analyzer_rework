// Co-authors API client (/api/coauthors/): invite links, the people who work in
// your workspace, and the workspaces you can work in. Session only. Errors are
// apiFetch's: the parsed body (`{detail, code, errors}`, or ninja's 422) with a
// non-enumerable `httpStatus`.
//
// An invite token only ever travels in a JSON POST body, never in a URL.
//
// Calls that change the session's workspace (switch, accept, leave) tell the
// other open tabs, which then reload under the new identity.
import { announceWorkspaceChange } from "../coauthors/workspace-sync";
import { apiFetch, ensureCsrfCookie } from "./core";

export interface CoauthorPerson {
  id: number;
  email: string;
  name: string;
  /** The cleaned name, or the e-mail when the name is blank. */
  display_name: string;
}

export interface CoauthorWorkspace {
  coauthorship_id: number;
  owner: CoauthorPerson;
  owner_is_staff: boolean;
  joined_at: string;
  last_active_at: string | null;
}

export interface CoauthorContext {
  /** The person who is signed in. */
  actor: CoauthorPerson;
  /** The workspace this session works in, or null for the person's own account. */
  acting_as: CoauthorWorkspace | null;
  workspaces: CoauthorWorkspace[];
}

export type InvitePreviewState = "valid" | "already_member" | "own_invite";

export interface InvitePreview {
  state: InvitePreviewState;
  inviter_name: string;
  /** Full address when signed in; masked (`a•••@example.org`) when signed out. */
  inviter_email: string;
  expires_at: string;
  authenticated: boolean;
  can_sign_up: boolean;
  coauthorship_id: number | null;
}

export interface AcceptResult {
  status: "joined" | "already_member";
  context: CoauthorContext;
}

export type InviteStatus = "active" | "expired" | "used_up" | "revoked";

export interface CoauthorInvite {
  id: number;
  label: string;
  token_prefix: string;
  status: InviteStatus;
  max_uses: number;
  uses: number;
  expires_at: string;
  created_at: string;
  revoked_at: string | null;
}

export interface CoauthorInviteCreated extends CoauthorInvite {
  /** The raw token: returned once, by the create call only. */
  token: string;
  /** `/join#<token>`; prepend the origin to get the link. */
  path: string;
}

export interface CreateInvitePayload {
  label?: string;
  expires_in_days?: number;
  max_uses?: number;
}

export type MemberStatus = "active" | "removed" | "left";

export interface CoauthorMember {
  coauthorship_id: number;
  member: CoauthorPerson;
  status: MemberStatus;
  joined_at: string;
  last_active_at: string | null;
  ended_at: string | null;
  invite_label: string;
}

export interface CoauthorAction {
  id: number;
  method: string;
  path: string;
  status_code: number;
  created_at: string;
}

/** `code` of an error body thrown by apiFetch (`acting_denied`, `invite_invalid`, …), or "". */
export function coauthorErrorCode(error: unknown): string {
  if (!error || typeof error !== "object") return "";
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : "";
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});

export async function getCoauthorContext(): Promise<CoauthorContext> {
  const res = await apiFetch("/coauthors/context/");
  return res.json();
}

/** Select a workspace to work in, or `null` for your own account. */
export async function switchWorkspace(
  coauthorshipId: number | null,
): Promise<CoauthorContext> {
  const res = await apiFetch(
    "/coauthors/workspace/",
    json({ coauthorship_id: coauthorshipId }),
  );
  announceWorkspaceChange();
  return res.json();
}

/** Who sent this invite, and whether it can be used. Works signed out. */
export async function previewInvite(token: string): Promise<InvitePreview> {
  // Signed-out visitors may not have a CSRF cookie yet; the route enforces CSRF.
  await ensureCsrfCookie();
  const res = await apiFetch("/coauthors/join/preview/", json({ token }));
  return res.json();
}

export async function acceptInvite(token: string): Promise<AcceptResult> {
  await ensureCsrfCookie();
  const res = await apiFetch("/coauthors/join/accept/", json({ token }));
  announceWorkspaceChange();
  return res.json();
}

/** Leave a workspace you were invited to. */
export async function leaveWorkspace(coauthorshipId: number): Promise<void> {
  await apiFetch(`/coauthors/memberships/${coauthorshipId}/`, {
    method: "DELETE",
  });
  announceWorkspaceChange();
}

export async function listInvites(): Promise<CoauthorInvite[]> {
  const res = await apiFetch("/coauthors/invites/");
  return res.json();
}

export async function createInvite(
  payload: CreateInvitePayload,
): Promise<CoauthorInviteCreated> {
  const res = await apiFetch("/coauthors/invites/", json(payload));
  return res.json();
}

export async function revokeInvite(id: number): Promise<void> {
  await apiFetch(`/coauthors/invites/${id}/`, { method: "DELETE" });
}

export async function listMembers(): Promise<CoauthorMember[]> {
  const res = await apiFetch("/coauthors/members/");
  return res.json();
}

export async function removeMember(coauthorshipId: number): Promise<void> {
  await apiFetch(`/coauthors/members/${coauthorshipId}/`, {
    method: "DELETE",
  });
}

/** What a co-author changed, newest first; page with `before` = the smallest id shown. */
export async function getMemberActivity(
  coauthorshipId: number,
  { limit = 50, before }: { limit?: number; before?: number } = {},
): Promise<CoauthorAction[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (before != null) params.set("before", String(before));
  const res = await apiFetch(
    `/coauthors/members/${coauthorshipId}/activity/?${params}`,
  );
  return res.json();
}
