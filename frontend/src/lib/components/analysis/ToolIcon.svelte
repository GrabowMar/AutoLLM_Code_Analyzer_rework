<script lang="ts">
	/**
	 * ToolIcon — a tool's brand mark (`toolLogos`, Simple Icons) on a brand
	 * tile, else its catalog Lucide icon (`toolIcon`: icon, then category).
	 *
	 * Props: `tool` ({slug, icon?, category?}), `size` (sm | md | lg), `class`.
	 */
	import { cn } from '$lib/utils';
	import { toolIcon } from './profile/profile-utils';
	import { toolLogos } from './toolLogos';

	interface Props {
		tool: { slug: string; icon?: string | null; category?: string | null };
		size?: 'sm' | 'md' | 'lg';
		class?: string;
	}

	let { tool, size = 'md', class: className }: Props = $props();

	const logo = $derived(toolLogos[tool.slug]);
	const IconComponent = $derived(toolIcon(tool.icon, tool.category));

	const tile = { sm: 'size-7 rounded', md: 'size-9 rounded-md', lg: 'size-11 rounded-md' };
	const glyph = { sm: 'size-4', md: 'size-5', lg: 'size-6' };
</script>

{#if logo}
	<span
		class={cn(
			'inline-flex shrink-0 items-center justify-center ring-1 ring-border',
			tile[size],
			className
		)}
		style:background-color={logo.hex}
		style:color={logo.fg}
		role="img"
		aria-label="{logo.title} logo"
	>
		<svg viewBox="0 0 24 24" class={glyph[size]} fill="currentColor" aria-hidden="true">
			<path d={logo.path} />
		</svg>
	</span>
{:else}
	<span
		class={cn(
			'inline-flex shrink-0 items-center justify-center border border-border bg-muted/50 text-foreground/80',
			tile[size],
			className
		)}
		aria-hidden="true"
	>
		<IconComponent class={glyph[size]} />
	</span>
{/if}
