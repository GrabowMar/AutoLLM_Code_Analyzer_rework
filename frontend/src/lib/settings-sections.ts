// Settings page sections: the left nav, the mobile picker and `/users/settings#<id>` deep links.
import type { Component } from "svelte";
import User from "@lucide/svelte/icons/user";
import Lock from "@lucide/svelte/icons/lock";
import Palette from "@lucide/svelte/icons/palette";
import LayoutDashboard from "@lucide/svelte/icons/layout-dashboard";
import ShieldCheck from "@lucide/svelte/icons/shield-check";
import KeyRound from "@lucide/svelte/icons/key-round";
import Cloud from "@lucide/svelte/icons/cloud";
import GitBranch from "@lucide/svelte/icons/git-branch";
import Bot from "@lucide/svelte/icons/bot";
import Bell from "@lucide/svelte/icons/bell";
import Terminal from "@lucide/svelte/icons/terminal";

export type SettingsGroup = "account" | "preferences" | "connections";

export interface SettingsSection {
  id: string;
  label: string;
  description: string;
  group: SettingsGroup;
  icon: Component;
  /** Extra words the settings search matches. */
  keywords?: string;
}

export const SETTINGS_GROUPS: {
  id: SettingsGroup;
  label: string;
  hint: string;
}[] = [
  { id: "account", label: "Account", hint: "Who you are and how you sign in." },
  {
    id: "preferences",
    label: "Preferences",
    hint: "How the app looks and behaves. Saved in this browser only.",
  },
  {
    id: "connections",
    label: "Connections",
    hint: "Keys, tokens and the services LLM Lab talks to.",
  },
];

export const SETTINGS_SECTIONS: SettingsSection[] = [
  {
    id: "account",
    label: "Profile",
    description: "Your display name and how you appear across LLM Lab.",
    group: "account",
    icon: User,
    keywords: "name display email avatar",
  },
  {
    id: "security",
    label: "Security",
    description: "Change the password you sign in with.",
    group: "account",
    icon: Lock,
    keywords: "password sign in login",
  },
  {
    id: "appearance",
    label: "Appearance",
    description: "Theme and the color of your initials avatar.",
    group: "preferences",
    icon: Palette,
    keywords: "theme dark light mode color avatar",
  },
  {
    id: "workspace",
    label: "Workspace",
    description: "Layout defaults for the sidebar and data tables.",
    group: "preferences",
    icon: LayoutDashboard,
    keywords: "sidebar tables density compact layout",
  },
  {
    id: "privacy",
    label: "Privacy & data",
    description:
      "What this app stores in your browser, and a backup of your preferences.",
    group: "preferences",
    icon: ShieldCheck,
    keywords: "cookies storage export import reset backup",
  },
  {
    id: "credentials",
    label: "API keys",
    description:
      "Your personal OpenRouter and Hugging Face keys, used for the jobs you submit.",
    group: "connections",
    icon: KeyRound,
    keywords: "openrouter hugging face hf token key",
  },
  {
    id: "providers",
    label: "LLM providers",
    description:
      "Connect a cloud provider with its API key: LLM Lab creates an endpoint and imports its models, ready for generation and experiments.",
    group: "connections",
    icon: Cloud,
    keywords: "anthropic openai gemini groq mistral endpoint cloud",
  },
  {
    id: "github",
    label: "GitHub",
    description:
      "Export generated applications to a repository or a Gist with your own token.",
    group: "connections",
    icon: GitBranch,
    keywords: "export repository gist sarif token",
  },
  {
    id: "mcp",
    label: "MCP server",
    description:
      "Let an AI assistant (Claude, ChatGPT, Cursor, VS Code…) drive LLM Lab over the Model Context Protocol.",
    group: "connections",
    icon: Bot,
    keywords: "claude chatgpt cursor vscode assistant model context protocol",
  },
  {
    id: "notifications",
    label: "Notifications",
    description:
      "Post to Slack, Discord, Teams, ntfy, Telegram or a signed JSON webhook when runs finish.",
    group: "connections",
    icon: Bell,
    keywords: "webhook slack discord teams ntfy telegram",
  },
  {
    id: "tokens",
    label: "Personal tokens",
    description:
      "Bearer tokens for scripts, CI and MCP clients. They act as you on the REST API.",
    group: "connections",
    icon: Terminal,
    keywords: "api bearer ci scripts rest",
  },
];

// Older deep links and the retired /integrations?tab=… page.
const ALIASES: Record<string, string> = {
  profile: "account",
  password: "security",
  data: "privacy",
  cookies: "privacy",
  "api-keys": "credentials",
  keys: "credentials",
  integrations: "mcp",
};

export function resolveSettingsSection(id: string | null | undefined): string {
  const key = (id ?? "").trim().toLowerCase();
  const resolved = ALIASES[key] ?? key;
  return SETTINGS_SECTIONS.some((s) => s.id === resolved)
    ? resolved
    : "account";
}
