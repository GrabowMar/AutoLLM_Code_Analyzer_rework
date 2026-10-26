<script lang="ts">
	/**
	 * LevelBadge — a finding's level exactly as its tool printed it ("HIGH",
	 * "error", "convention"). Tools that rate nothing get no badge (or the
	 * `fallback` text): the normalized severity is never shown.
	 *
	 * Props: `label` (`tool_severity`), `fallback` (shown when empty, muted), `class`.
	 */
	import { cn } from '$lib/utils';
	import { levelClasses } from './format';

	interface Props {
		label: string | null | undefined;
		fallback?: string;
		class?: string;
	}

	let { label, fallback = '', class: className }: Props = $props();
	const text = $derived((label ?? '').trim());
</script>

{#if text}
	<span
		class={cn(
			'inline-flex shrink-0 items-center whitespace-nowrap rounded border px-1.5 py-px font-mono text-[11px] leading-4',
			levelClasses(text),
			className
		)}
		title="Level reported by the tool"
	>
		{text}
	</span>
{:else if fallback}
	<span class={cn('text-[11px] text-muted-foreground', className)} title="This tool doesn't rate its findings">{fallback}</span>
{/if}
