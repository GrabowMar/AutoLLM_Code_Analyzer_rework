<script lang="ts">
	/**
	 * Shows a provider's favicon logo, falling back to a colored initial badge.
	 * Uses Google's favicon service — tiny images, browser-cached per domain.
	 */

	interface Props {
		/** Provider slug as stored in the DB (e.g. "openai", "anthropic", "meta-llama") */
		provider: string;
		/** Display size in px — used for both the img and fallback badge */
		size?: number;
		class?: string;
	}

	// Map known AI provider slugs → their website domains
	const DOMAINS: Record<string, string> = {
		openai: 'openai.com',
		anthropic: 'anthropic.com',
		google: 'google.com',
		'google-deepmind': 'deepmind.google',
		'meta-llama': 'meta.com',
		meta: 'meta.com',
		mistralai: 'mistral.ai',
		mistral: 'mistral.ai',
		cohere: 'cohere.com',
		microsoft: 'microsoft.com',
		amazon: 'amazon.com',
		nvidia: 'nvidia.com',
		deepseek: 'deepseek.com',
		'x-ai': 'x.ai',
		xai: 'x.ai',
		'perplexity-ai': 'perplexity.ai',
		perplexity: 'perplexity.ai',
		'01-ai': '01.ai',
		qwen: 'qwen.ai',
		databricks: 'databricks.com',
		together: 'together.ai',
		groq: 'groq.com',
		fireworks: 'fireworks.ai',
		nousresearch: 'nousresearch.com',
		'openchat': 'openchat.team',
		'liquid': 'liquid.ai',
		'inflection': 'inflection.ai',
		'ai21': 'ai21.com',
		'writer': 'writer.com',
		'phind': 'phind.com',
		'neversleep': 'neversleep.ai',
		'gryphe': 'gryphe.com',
	};

	// Deterministic color for the fallback badge based on provider name
	const PALETTE = [
		'#3b82f6', '#10b981', '#8b5cf6', '#f59e0b',
		'#ef4444', '#06b6d4', '#f97316', '#6366f1',
	];

	function hashColor(str: string): string {
		let h = 0;
		for (let i = 0; i < str.length; i++) h = str.charCodeAt(i) + ((h << 5) - h);
		return PALETTE[Math.abs(h) % PALETTE.length];
	}

	let { provider, size = 20, class: cls = '' }: Props = $props();

	const key = $derived(provider.toLowerCase().trim());
	const domain = $derived(DOMAINS[key] ?? null);
	const faviconUrl = $derived(
		domain ? `https://www.google.com/s2/favicons?domain=${domain}&sz=${Math.max(size, 32)}` : null
	);
	const initials = $derived(
		provider
			.replace(/[-_]/g, ' ')
			.split(' ')
			.slice(0, 2)
			.map((w) => w[0]?.toUpperCase() ?? '')
			.join('')
			.slice(0, 2)
	);
	const badgeColor = $derived(hashColor(provider));

	let failed = $state(false);
</script>

{#if faviconUrl && !failed}
	<img
		src={faviconUrl}
		alt="{provider} logo"
		width={size}
		height={size}
		loading="lazy"
		decoding="async"
		class="rounded-sm object-contain {cls}"
		onerror={() => { failed = true; }}
	/>
{:else}
	<span
		class="inline-flex shrink-0 items-center justify-center rounded-sm font-bold text-white leading-none {cls}"
		style="background:{badgeColor}; width:{size}px; height:{size}px; font-size:{Math.round(size * 0.42)}px;"
		aria-label="{provider} logo"
	>
		{initials}
	</span>
{/if}
