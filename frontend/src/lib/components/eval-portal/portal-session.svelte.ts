// Participant-portal session: who is using the portal right now, from
// GET /api/human-eval/portal/me/ (the cookie participant of a link / code
// session and/or the signed-in account with its account and preview
// participations), plus sign-out. Sign-out always ends the cookie session
// (portal logout/: clears the cookie and its server-side token) and, for
// accounts, also the account session (allauth logout), and forgets what the
// workbench keeps in this browser (HTTP console history and headers).
import { apiErrorStatus } from "$lib/api/core";
import { getPortalMe, portalLogout } from "$lib/api/human-eval-portal";
import type { PortalMe } from "$lib/api/types/human-eval";
import { getAuth } from "$lib/stores/auth.svelte";
import { portalErrorMessage } from "./portal";
import { clearConsoleStorage } from "./workbench/workbench";

export function anonymousMe(): PortalMe {
  return {
    kind: "anonymous",
    participant: null,
    user: null,
    participations: [],
  };
}

export interface PortalSessionDeps {
  fetchMe: () => Promise<PortalMe>;
  logoutPortal: () => Promise<void>;
  logoutAccount: () => Promise<void>;
  /** The app-wide auth store's view (the root layout checks the allauth session). */
  accountSignedIn: () => boolean;
  /** Forget participant data kept in this browser (shared lab computers). */
  clearLocalData?: () => void;
}

export class PortalSession {
  me = $state<PortalMe | null>(null);
  loading = $state(false);
  error = $state("");
  #seq = 0;
  #deps: PortalSessionDeps;

  constructor(deps: PortalSessionDeps) {
    this.#deps = deps;
  }

  /** A signed-in account is present (evaluator, researcher, or both identities). */
  get isAccount(): boolean {
    return !!this.me?.user;
  }

  /** This browser holds a link / code participant session. */
  get hasParticipantSession(): boolean {
    return !!this.me?.participant;
  }

  /** Reload `me`; returns it, or null when the request failed (see `error`). */
  async refresh(): Promise<PortalMe | null> {
    const mine = ++this.#seq;
    this.loading = true;
    try {
      const me = await this.#deps.fetchMe();
      if (mine === this.#seq) {
        this.me = me;
        this.error = "";
      }
      return me;
    } catch (e) {
      if (mine === this.#seq) {
        this.error = portalErrorMessage(e, "Could not check your session.");
      }
      return null;
    } finally {
      if (mine === this.#seq) this.loading = false;
    }
  }

  /**
   * End the cookie session and, for accounts, the account session. Local state
   * (and the data kept in this browser) is cleared even when a request fails;
   * the first real failure is re-thrown.
   */
  async signOut(): Promise<void> {
    const hadAccount = !!this.me?.user || this.#deps.accountSignedIn();
    this.#seq += 1; // results of refreshes still in flight are stale now
    let failure: unknown = null;
    try {
      await this.#deps.logoutPortal();
    } catch (e) {
      // 401: nothing to end.
      if (apiErrorStatus(e) !== 401) failure = e;
    }
    if (hadAccount) {
      try {
        await this.#deps.logoutAccount();
      } catch (e) {
        failure ??= e;
      }
    }
    this.#deps.clearLocalData?.();
    this.me = anonymousMe();
    this.loading = false;
    this.error = "";
    if (failure) throw failure;
  }

  /**
   * Drop a participation locally (after withdrawing: the server already cleared
   * the cookie session), then reload.
   */
  async forgetTask(taskId: string): Promise<void> {
    const me = this.me;
    if (me) {
      this.me = {
        ...me,
        participant: me.participant?.task_id === taskId ? null : me.participant,
        participations: me.participations.filter((p) => p.task_id !== taskId),
      };
    }
    await this.refresh();
  }
}

let instance: PortalSession | null = null;

export function getPortalSession(): PortalSession {
  if (!instance) {
    const auth = getAuth();
    instance = new PortalSession({
      fetchMe: () => getPortalMe(),
      logoutPortal: () => portalLogout(),
      logoutAccount: () => auth.logout(),
      accountSignedIn: () => auth.isAuthenticated,
      clearLocalData: () => clearConsoleStorage(),
    });
  }
  return instance;
}
