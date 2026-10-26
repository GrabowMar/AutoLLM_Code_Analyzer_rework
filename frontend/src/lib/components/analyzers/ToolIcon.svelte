<script lang="ts" module>
	import Activity from '@lucide/svelte/icons/activity';
	import Atom from '@lucide/svelte/icons/atom';
	import BadgeCheck from '@lucide/svelte/icons/badge-check';
	import Bot from '@lucide/svelte/icons/bot';
	import Boxes from '@lucide/svelte/icons/boxes';
	import Braces from '@lucide/svelte/icons/braces';
	import CheckCheck from '@lucide/svelte/icons/check-check';
	import Container from '@lucide/svelte/icons/container';
	import Copy from '@lucide/svelte/icons/copy';
	import FileCode from '@lucide/svelte/icons/file-code';
	import Gauge from '@lucide/svelte/icons/gauge';
	import Key from '@lucide/svelte/icons/key';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import ListChecks from '@lucide/svelte/icons/list-checks';
	import Package from '@lucide/svelte/icons/package';
	import PackageCheck from '@lucide/svelte/icons/package-check';
	import PackageSearch from '@lucide/svelte/icons/package-search';
	import PackageX from '@lucide/svelte/icons/package-x';
	import Palette from '@lucide/svelte/icons/palette';
	import Puzzle from '@lucide/svelte/icons/puzzle';
	import Radar from '@lucide/svelte/icons/radar';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import Skull from '@lucide/svelte/icons/skull';
	import Snowflake from '@lucide/svelte/icons/snowflake';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import SpellCheck from '@lucide/svelte/icons/spell-check';
	import Terminal from '@lucide/svelte/icons/terminal';
	import Zap from '@lucide/svelte/icons/zap';

	type Icon = typeof Puzzle;

	/** Lucide icons a catalog tool may name in its YAML `icon` field. */
	const icons: Record<string, Icon> = {
		activity: Activity,
		atom: Atom,
		'badge-check': BadgeCheck,
		bot: Bot,
		boxes: Boxes,
		braces: Braces,
		'check-check': CheckCheck,
		container: Container,
		copy: Copy,
		'file-code': FileCode,
		gauge: Gauge,
		key: Key,
		'key-round': KeyRound,
		'package-check': PackageCheck,
		'package-search': PackageSearch,
		'package-x': PackageX,
		palette: Palette,
		radar: Radar,
		'shield-alert': ShieldAlert,
		'shield-check': ShieldCheck,
		skull: Skull,
		snowflake: Snowflake,
		sparkles: Sparkles,
		'spell-check': SpellCheck,
		terminal: Terminal,
		zap: Zap,
	};

	/** Default per tool type, for tools whose `icon` is empty or unknown. */
	const categoryIcons: Record<string, Icon> = {
		security: ShieldAlert,
		lint: ListChecks,
		performance: Gauge,
		secrets: KeyRound,
		dependencies: Package,
		ai: Bot,
	};
</script>

<script lang="ts">
	import { cn } from '$lib/utils';
	import { toolLogos } from './toolLogos';

	interface Props {
		tool: { slug: string; icon?: string; category?: string; name?: string };
		size?: 'sm' | 'md' | 'lg';
		class?: string;
	}

	let { tool, size = 'md', class: className }: Props = $props();

	const logo = $derived(toolLogos[tool.slug]);
	const IconComponent = $derived(icons[tool.icon ?? ''] ?? categoryIcons[tool.category ?? ''] ?? Puzzle);

	const tile = { sm: 'size-7 rounded', md: 'size-9 rounded-md', lg: 'size-11 rounded-md' };
	const glyph = { sm: 'size-4', md: 'size-5', lg: 'size-6' };
</script>

{#if logo}
	<span
		class={cn('inline-flex shrink-0 items-center justify-center ring-1 ring-border', tile[size], className)}
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
			'inline-flex shrink-0 items-center justify-center border bg-muted/60 text-foreground/80',
			tile[size],
			className
		)}
		aria-hidden="true"
	>
		<IconComponent class={glyph[size]} />
	</span>
{/if}
