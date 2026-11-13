<script lang="ts">
	/**
	 * "Columns" menu of the models table: tick columns on/off (grouped), pick which capability
	 * icons the Capabilities column shows, reset to the default table. Stays open while toggling.
	 */
	import { DropdownMenu } from 'bits-ui';
	import Columns3 from '@lucide/svelte/icons/columns-3';
	import Check from '@lucide/svelte/icons/check';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Brain from '@lucide/svelte/icons/brain';
	import Wrench from '@lucide/svelte/icons/wrench';
	import Eye from '@lucide/svelte/icons/eye';
	import Braces from '@lucide/svelte/icons/braces';
	import Radio from '@lucide/svelte/icons/radio';
	import { COLUMNS, columnPrefs, type CapabilityKey, type ColumnDef } from './columns.svelte';

	const GROUPS: ColumnDef['group'][] = ['Basics', 'Limits & price', 'Research'];
	const CAP_ITEMS: { key: CapabilityKey; label: string; icon: typeof Eye }[] = [
		{ key: 'supports_reasoning', label: 'Reasoning', icon: Brain },
		{ key: 'supports_function_calling', label: 'Tool calling', icon: Wrench },
		{ key: 'supports_vision', label: 'Vision', icon: Eye },
		{ key: 'supports_json_mode', label: 'JSON mode', icon: Braces },
		{ key: 'supports_streaming', label: 'Streaming', icon: Radio },
	];

	const shown = $derived(columnPrefs.columns.length);
	const item =
		'flex min-w-0 cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1 text-[13px] outline-none data-[highlighted]:bg-surface-2 data-[disabled]:opacity-50';
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger class="fm-trigger h-7! text-xs" aria-label="Choose table columns">
		<Columns3 class="size-3.5" aria-hidden="true" />Columns
		{#if !columnPrefs.isDefault}<span class="cm-dot" title="Customised ({shown + 1} columns)"></span>{/if}
	</DropdownMenu.Trigger>
	<DropdownMenu.Portal>
		<DropdownMenu.Content
			align="end"
			sideOffset={6}
			collisionPadding={12}
			class="z-50 max-h-[min(34rem,var(--bits-dropdown-menu-content-available-height))] w-80 overflow-y-auto rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md"
		>
			<p class="px-2 pb-1 pt-1.5 text-[11px] text-muted-foreground">Model name is always shown.</p>
			{#each GROUPS as group (group)}
				<DropdownMenu.Group>
					<DropdownMenu.GroupHeading class="section-label px-2 pb-0.5 pt-2 text-[11px]!">{group}</DropdownMenu.GroupHeading>
					<div class="grid grid-cols-2">
					{#each COLUMNS.filter((c) => c.group === group) as col (col.key)}
						<DropdownMenu.CheckboxItem
							class={item}
							checked={columnPrefs.columns.includes(col.key)}
							onCheckedChange={() => columnPrefs.toggleColumn(col.key)}
							closeOnSelect={false}
						>
							{#snippet children({ checked })}
								<span class="cm-box" data-on={checked}>{#if checked}<Check class="size-3" />{/if}</span>
								<span class="truncate" title={col.help}>{col.menuLabel ?? col.label}</span>
							{/snippet}
						</DropdownMenu.CheckboxItem>
					{/each}
					</div>
				</DropdownMenu.Group>
			{/each}

			{#if columnPrefs.columns.includes('capabilities')}
				<DropdownMenu.Separator class="-mx-1 my-1 h-px bg-border" />
				<DropdownMenu.Group>
					<DropdownMenu.GroupHeading class="section-label px-2 pb-0.5 pt-1 text-[11px]!">Capability icons</DropdownMenu.GroupHeading>
					<div class="grid grid-cols-2">
					{#each CAP_ITEMS as cap (cap.key)}
						<DropdownMenu.CheckboxItem
							class={item}
							checked={columnPrefs.capabilities.includes(cap.key)}
							onCheckedChange={() => columnPrefs.toggleCapability(cap.key)}
							closeOnSelect={false}
						>
							{#snippet children({ checked })}
								<span class="cm-box" data-on={checked}>{#if checked}<Check class="size-3" />{/if}</span>
								<cap.icon class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" /><span class="truncate">{cap.label}</span>
							{/snippet}
						</DropdownMenu.CheckboxItem>
					{/each}
					</div>
				</DropdownMenu.Group>
			{/if}

			<DropdownMenu.Separator class="-mx-1 my-1 h-px bg-border" />
			<DropdownMenu.Item class={item} disabled={columnPrefs.isDefault} onSelect={() => columnPrefs.reset()} closeOnSelect={false}>
				<RotateCcw class="size-3.5 text-muted-foreground" aria-hidden="true" />Reset to default
				<span class="ml-auto font-mono text-[11px] text-muted-foreground tabular-nums">{shown + 1} / {COLUMNS.length + 1}</span>
			</DropdownMenu.Item>
		</DropdownMenu.Content>
	</DropdownMenu.Portal>
</DropdownMenu.Root>

<style>
	.cm-dot {
		width: 0.375rem;
		height: 0.375rem;
		border-radius: 9999px;
		background: var(--primary);
	}
	.cm-box {
		display: inline-flex;
		width: 0.875rem;
		height: 0.875rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: 0.1875rem;
		border: 1px solid var(--input);
		color: var(--primary-foreground);
	}
	.cm-box[data-on='true'] {
		border-color: var(--primary);
		background: var(--primary);
	}
</style>
