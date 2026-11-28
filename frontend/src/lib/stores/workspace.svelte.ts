// Which workspace this session works in: the signed-in person's own account,
// or the workspace of someone who invited them as a co-author. Loaded by the
// app shell once the role check is done. Switching reloads the whole app,
// which is the cache reset: every page then loads as the new identity.
//
// When access to the selected workspace ends (the owner removed this person,
// or their account went away), the server answers one request with 409
// `workspace_ended` and then serves the person's own account. apiFetch flags
// that in sessionStorage; this store also notices it on its own (it remembers
// which workspace this tab was in), so the shell can say what happened even if
// the 409 went to a request that did not go through apiFetch.
//
// The choice is per session, so it is shared by every tab: the store tells
// apiFetch which workspace this tab shows (`X-Workspace`), and `watchOtherTabs`
// reloads this tab when another one switches (see workspace-sync.ts).
import {
  getCoauthorContext,
  switchWorkspace,
  type CoauthorPerson,
  type CoauthorWorkspace,
} from "$lib/api/coauthors";
import {
  onWorkspaceChange,
  reloadHere,
  setWorkspaceClaim,
} from "$lib/coauthors/workspace-sync";

/** Set by apiFetch on a 409 `workspace_ended`, read once by the app shell. */
export const ENDED_KEY = "coauthor.ended";
/** `<actor id>:<coauthorship id>` of the workspace this tab works in. */
const ACTING_KEY = "coauthor.acting";

function readSession(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSession(key: string, value: string | null) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    // blocked storage
  }
}

function createWorkspace() {
  let loaded = $state(false);
  let actor = $state<CoauthorPerson | null>(null);
  let actingAs = $state<CoauthorWorkspace | null>(null);
  let workspaces = $state<CoauthorWorkspace[]>([]);
  let pending: Promise<void> | null = null;

  async function fetchContext() {
    try {
      const context = await getCoauthorContext();
      actor = context.actor;
      actingAs = context.acting_as;
      workspaces = context.workspaces;
      setWorkspaceClaim(context.acting_as?.coauthorship_id ?? null);
      const was = readSession(ACTING_KEY);
      if (context.acting_as) {
        writeSession(
          ACTING_KEY,
          `${context.actor.id}:${context.acting_as.coauthorship_id}`,
        );
      } else if (was !== null) {
        writeSession(ACTING_KEY, null);
        // This tab was in a workspace that is no longer usable (and not because
        // the person switched away, e.g. in another tab): access ended.
        const [actorId, coauthorshipId] = was.split(":").map(Number);
        const stillUsable = context.workspaces.some(
          (w) => w.coauthorship_id === coauthorshipId,
        );
        if (actorId === context.actor.id && !stillUsable) {
          writeSession(ENDED_KEY, "1");
        }
      }
    } catch {
      // Safe default: the shell renders as the person's own account. (A 409
      // `workspace_ended` has already sent the browser home from apiFetch.)
      actingAs = null;
      workspaces = [];
      setWorkspaceClaim(undefined);
    } finally {
      loaded = true;
    }
  }

  function load(): Promise<void> {
    pending ??= fetchContext().finally(() => {
      pending = null;
    });
    return pending;
  }

  /** Forget the loaded context (the app shell calls this before loading it for a newly signed-in person). */
  function reset() {
    loaded = false;
    actor = null;
    actingAs = null;
    workspaces = [];
    setWorkspaceClaim(undefined);
  }

  /** Reload this tab when another tab of the session switches workspace; returns the cleanup. */
  function watchOtherTabs(): () => void {
    return onWorkspaceChange(reloadHere);
  }

  /** Work in another workspace (`null` = my own account), then reload the app. */
  async function switchTo(coauthorshipId: number | null): Promise<void> {
    const context = await switchWorkspace(coauthorshipId);
    writeSession(
      ACTING_KEY,
      context.acting_as
        ? `${context.actor.id}:${context.acting_as.coauthorship_id}`
        : null,
    );
    window.location.assign("/");
  }

  /** True once after access to the selected workspace ended (see the module comment). */
  function consumeEnded(): boolean {
    const ended = readSession(ENDED_KEY) === "1";
    if (ended) writeSession(ENDED_KEY, null);
    return ended;
  }

  return {
    get loaded() {
      return loaded;
    },
    get actor() {
      return actor;
    },
    get actingAs() {
      return actingAs;
    },
    get workspaces() {
      return workspaces;
    },
    load,
    reset,
    switchTo,
    consumeEnded,
    watchOtherTabs,
  };
}

let instance: ReturnType<typeof createWorkspace> | null = null;

export function getWorkspace() {
  instance ??= createWorkspace();
  return instance;
}
