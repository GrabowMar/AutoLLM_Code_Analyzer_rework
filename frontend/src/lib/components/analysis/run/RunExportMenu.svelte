<script lang="ts">
	/**
	 * RunExportMenu — downloads of one run: everything (JSON: plan, target,
	 * tool results, findings, measurement), findings (CSV) and SARIF 2.1.0
	 * (real tool versions, `executionSuccessful` per tool).
	 *
	 * Props: `runId`, `partial` (the run has not finished: say so).
	 */
	import { DropdownMenu } from 'bits-ui';
	import { runExportUrl, type RunExportFormat } from '$lib/api/analysis';
	import { buttonClass } from './button-class';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import Download from '@lucide/svelte/icons/download';

	interface Props {
		runId: string;
		partial?: boolean;
	}

	let { runId, partial = false }: Props = $props();

	const formats: { format: RunExportFormat; label: string; hint: string }[] = [
		{
			format: 'json',
			label: 'Run (JSON)',
			hint: 'Plan, target, tool results, findings, measurement'
		},
		{
			format: 'csv',
			label: 'Findings (CSV)',
			hint: 'One row per finding, with provenance columns'
		},
		{ format: 'sarif', label: 'SARIF 2.1.0', hint: 'For code-scanning tools' }
	];
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger class={buttonClass}>
		<Download class="size-3.5" aria-hidden="true" />Export
		<ChevronDown class="size-3" aria-hidden="true" />
	</DropdownMenu.Trigger>
	<DropdownMenu.Portal>
		<DropdownMenu.Content
			align="end"
			sideOffset={6}
			class="z-50 w-64 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md"
		>
			{#if partial}
				<p class="px-2 py-1.5 text-[11px] text-warning-fg">
					The run has not finished: exports are partial.
				</p>
			{/if}
			{#each formats as item (item.format)}
				<DropdownMenu.Item
					class="cursor-pointer rounded px-2 py-1.5 outline-none data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground"
				>
					{#snippet child({ props })}
						<a
							{...props}
							href={runExportUrl(runId, item.format)}
							download
							class="{props.class as string} block"
						>
							<span class="block text-sm font-medium">{item.label}</span>
							<span class="block text-[11px] text-muted-foreground">{item.hint}</span>
						</a>
					{/snippet}
				</DropdownMenu.Item>
			{/each}
		</DropdownMenu.Content>
	</DropdownMenu.Portal>
</DropdownMenu.Root>
