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
		cyan: 'var(--accent-2)',
		violet: 'var(--accent-3)',
		amber: 'var(--warning)',
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
	<div class="kpi-card h-full">
		<div class="flex items-start justify-between gap-2">
			<span class="kpi-label">{title}</span>
			<span
				class="flex h-7 w-7 items-center justify-center rounded-lg sm:h-8 sm:w-8"
				style="color: {accentVar[accent]}; background: color-mix(in oklab, {accentVar[
					accent
				]} 12%, transparent);"
			>
				<Icon class="h-3.5 w-3.5 sm:h-4 sm:w-4" />
			</span>
		</div>
		<div class="kpi-value">{display}</div>
		<p class="text-[11px] text-muted-foreground sm:text-xs">{subtitle}</p>
		{#if change}
			<p class="text-[11px] sm:text-xs" style="color: {accentVar[accent]};">{change}</p>
		{/if}
		<span
			class="absolute bottom-3 right-4 opacity-0 transition-opacity group-hover:opacity-100"
			aria-hidden="true"
		>
			<ArrowRight class="h-4 w-4 text-muted-foreground" />
		</span>
	</div>
</a>
