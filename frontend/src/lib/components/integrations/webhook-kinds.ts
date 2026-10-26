import type { WebhookKind } from "$lib/api/integrations";

export interface WebhookKindInfo {
  value: WebhookKind;
  label: string;
  /** Whether the channel is addressed by a URL (all but Telegram). */
  usesUrl: boolean;
  urlPlaceholder: string;
  urlHelp: string;
  /** Meaning of `secret` for this kind; null when it has none. */
  secret: { label: string; help: string; required: boolean } | null;
}

export const WEBHOOK_KINDS: WebhookKindInfo[] = [
  {
    value: "slack",
    label: "Slack",
    usesUrl: true,
    urlPlaceholder: "https://hooks.slack.com/services/…",
    urlHelp: "An incoming-webhook URL (Slack app → Incoming Webhooks).",
    secret: null,
  },
  {
    value: "discord",
    label: "Discord",
    usesUrl: true,
    urlPlaceholder: "https://discord.com/api/webhooks/…",
    urlHelp: "Channel settings → Integrations → Webhooks → Copy Webhook URL.",
    secret: null,
  },
  {
    value: "teams",
    label: "Microsoft Teams",
    usesUrl: true,
    urlPlaceholder: "https://…/workflows/…",
    urlHelp:
      'A Workflows "Post to a channel when a webhook request is received" URL.',
    secret: null,
  },
  {
    value: "ntfy",
    label: "ntfy",
    usesUrl: true,
    urlPlaceholder: "https://ntfy.sh/<topic>",
    urlHelp:
      "The topic URL on ntfy.sh or your own server. Pick a hard-to-guess topic.",
    secret: {
      label: "Access token",
      help: "Only for protected topics (tk_…).",
      required: false,
    },
  },
  {
    value: "telegram",
    label: "Telegram",
    usesUrl: false,
    urlPlaceholder: "",
    urlHelp: "",
    secret: {
      label: "Bot token",
      help: "From @BotFather (123456:ABC…). Add the bot to the chat first.",
      required: true,
    },
  },
  {
    value: "generic",
    label: "Generic JSON (signed)",
    usesUrl: true,
    urlPlaceholder: "https://example.com/hooks/llm-lab",
    urlHelp: "Receives a JSON POST for every event.",
    secret: {
      label: "Signing secret",
      help: "Optional. Each request carries X-LLMLab-Signature: sha256=<HMAC-SHA256 of the body>.",
      required: false,
    },
  },
];

export function webhookKind(kind: string): WebhookKindInfo | undefined {
  return WEBHOOK_KINDS.find((k) => k.value === kind);
}
