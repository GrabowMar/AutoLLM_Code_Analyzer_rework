<script lang="ts">
	import type { Component } from 'svelte';
	import { Tween } from 'svelte/motion';
	import { prefersReducedMotion } from 'svelte/motion';
	import { cubicOut } from 'svelte/easing';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';

	type Accent = 'green' | 'cyan' | 'violet' | 'amber';

	interface Props {
		title: string;
		value: number | null;
		subtitle: string;
		change?: string;
		icon: Component;
		href: string;
		accent?: Accent;
	}

	let { title, value, subtitle, change = '', icon: Icon, href, accent = 'green' }: Props = $props();

	const accentVar: Record<Accent, string> = {
		green: 'var(--primary)',
		cyan: 'var(--accent-3)',
		violet: 'var(--chart-5)',
		amber: 'var(--accent-2)',
	};

	// Count-up: animate only between real values; first value and
	// reduced-motion render instantly.
	const tween = new Tween(0, { duration: 400, easing: cubicOut });
	let hasValue = $state(false);

	$effect(() => {
		if (value === null) return;
		if (!hasValue || prefersReducedMotion.current) {
			tween.set(value, { duration: 0 });
			hasValue = true;
		} else {
			tween.set(value);
		}
	});

	const display = $derived(value === null ? '—' : Math.round(tween.current).toLocaleString());
</script>

<a {href} class="group block h-full">
	<div class="kpi-card h-full" style:--kpi-accent={accentVar[accent]}>
		<div class="flex items-start justify-between gap-2">
			<span class="kpi-label">{title}</span>
			<Icon class="h-4 w-4 text-muted-foreground transition-colors group-hover:text-foreground" aria-hidden="true" />
		</div>
		<div class="kpi-value">{display}</div>
		<p class="text-xs text-muted-foreground">{subtitle}</p>
		{#if change}
			<p class="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
				<span class="h-1.5 w-1.5 rounded-full" style:background={accentVar[accent]} aria-hidden="true"></span>
				{change}
			</p>
		{/if}
		<span
			class="absolute bottom-3 right-4 opacity-0 transition-opacity group-hover:opacity-100"
			aria-hidden="true"
		>
			<ArrowRight class="h-4 w-4 text-primary" />
		</span>
	</div>
</a>
