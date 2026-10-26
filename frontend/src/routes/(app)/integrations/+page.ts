import { redirect } from "@sveltejs/kit";
import { resolveSettingsSection } from "$lib/settings-sections";
import type { PageLoad } from "./$types";

export const ssr = false;

// Integrations moved into Settings → Connections; keep old links (?tab=github) working.
export const load: PageLoad = ({ url }) => {
  const tab = url.searchParams.get("tab");
  const section =
    tab === "notifications"
      ? "notifications"
      : resolveSettingsSection(tab ?? "mcp");
  redirect(308, `/users/settings#${section}`);
};
