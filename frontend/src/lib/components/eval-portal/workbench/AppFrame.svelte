<script lang="ts" module>
	export type FrameWidth = 'full' | 'tablet' | 'phone';
</script>

<script lang="ts">
	/**
	 * AppFrame — one app in the viewer: the running app in a sandboxed iframe
	 * (no top navigation, no referrer; only https apps on another origin than the
	 * portal are framed), or its HostingCard while it is not running / cannot be
	 * framed. `width` simulates tablet / phone widths; changing `reloadKey`
	 * reloads the frame.
	 *
	 * Props: `app`, `hosting`, `decision`, `width`, `reloadKey`, `readOnly`,
	 * `busy`, `onstart()`, `onopen()`, `class`.
	 */
	import type { HostingInfo, PortalApp } from '$lib/api/types/human-eval';
	import { cn } from '$lib/utils';
	import HostingCard from './HostingCard.svelte';
	import { IFRAME_SANDBOX, type FrameDecision } from './hosting';
	import { slotTitle } from './workbench';


	interface Props {
		app: Pick<PortalApp, 'slot' | 'label'>;
		hosting: HostingInfo;
		decision: FrameDecision;
		width?: FrameWidth;
		reloadKey?: number;
		readOnly?: boolean;
		busy?: boolean;
		onstart?: () => void;
		onopen?: () => void;
		class?: string;
	}

	let {
		app,
		hosting,
		decision,
		width = 'full',
		reloadKey = 0,
		readOnly = false,
		busy = false,
		onstart,
		onopen,
		class: className,
	}: Props = $props();

	const framed = $derived(hosting.state === 'running' && decision.canFrame && !!decision.url);
	const maxWidth = $derived(width === 'phone' ? '390px' : width === 'tablet' ? '820px' : '100%');
</script>

<div class={cn('flex h-full min-h-0 w-full min-w-0 flex-col', className)}>
	{#if framed}
		<div class="mx-auto h-full w-full min-h-0" style="max-width: {maxWidth}">
			{#key reloadKey}
				<iframe
					src={decision.url}
					title="{slotTitle(app)} — the app you are evaluating"
					sandbox={IFRAME_SANDBOX}
					referrerpolicy="no-referrer"
					class="h-full w-full rounded-md border border-border bg-white"
				></iframe>
			{/key}
		</div>
	{:else}
		<div class="flex h-full min-h-[14rem] items-center justify-center p-3">
			<HostingCard {app} {hosting} {decision} {readOnly} {busy} {onstart} {onopen} />
		</div>
	{/if}
</div>
