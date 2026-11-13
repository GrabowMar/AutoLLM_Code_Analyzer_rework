<script lang="ts">
	/**
	 * CapsCard — spend caps (spec 9.8): the caps that apply to the user (their own user caps, and the
	 * staff caps on endpoints, models and the platform), per period (5 h and week windows for subscription
	 * quotas, day, month) and unit (billed USD, list-price USD, tokens). Users add and lower their own caps,
	 * never above a staff cap; staff manage every scope.
	 *
	 * Props: `isStaff`, `onchanged()`.
	 */
	import { onMount } from 'svelte';
	import { createSpendCap, deleteSpendCap, listSpendCaps, updateSpendCap } from '$lib/api/v3/budget';
	import type { SpendCapIn, SpendCapOut, SpendPeriod, SpendUnit } from '$lib/api/v3/types';
	import { formatApiError } from '$lib/api/core';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import NumberField from '../shared/NumberField.svelte';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';

	interface Props {
		isStaff?: boolean;
		onchanged?: () => void;
	}

	let { isStaff = false, onchanged }: Props = $props();

	let caps = $state<SpendCapOut[] | null>(null);
	let error = $state('');
	let busy = $state(false);
	let draft = $state<SpendCapIn>({ scope: 'user', period: 'month', unit: 'usd', limit: 20, action: 'block' });

	const PERIODS: { value: SpendPeriod; label: string }[] = [
		{ value: '5h', label: '5 hours (quota window)' },
		{ value: 'day', label: 'Day' },
		{ value: 'week', label: 'Week (quota window)' },
		{ value: 'month', label: 'Month' },
	];
	const UNITS: { value: SpendUnit; label: string }[] = [
		{ value: 'usd', label: 'Billed USD' },
		{ value: 'list_usd', label: 'List-price USD' },
		{ value: 'tokens', label: 'Tokens' },
	];

	async function load() {
		error = '';
		try {
			caps = await listSpendCaps();
		} catch (e) {
			error = formatApiError(e, 'Could not load the caps.');
		}
	}

	onMount(load);

	async function run(fn: () => Promise<unknown>, fallback: string) {
		busy = true;
		error = '';
		try {
			await fn();
			await load();
			onchanged?.();
		} catch (e) {
			error = formatApiError(e, fallback);
		} finally {
			busy = false;
		}
	}

	const canEdit = (cap: SpendCapOut) => isStaff || (cap.scope === 'user' && !cap.staff_cap);
	const scopeLabel = (cap: SpendCapOut) =>
		cap.scope === 'user' ? (cap.staff_cap ? 'You (set by staff)' : 'You') : cap.scope === 'model' ? `Model ${cap.model_slug}` : cap.scope === 'endpoint' ? `Endpoint #${cap.endpoint}` : 'Platform';
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Header class="border-b border-border py-3">
		<Card.Title>Spend caps</Card.Title>
		<Card.Description>A blocking cap pauses experiments that would cross it (reason "spend cap", or "quota" for 5 h / week model caps); running runs finish.</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-3 p-0">
		{#if error}<div class="px-4 pt-3"><ErrorAlert message={error} onretry={load} /></div>{/if}
		{#if !caps}
			<LoadingState label="loading caps" />
		{:else}
			<div class="overflow-x-auto">
				<table>
					<thead><tr><th>Scope</th><th>Period</th><th>Unit</th><th class="num">Limit</th><th>Action</th><th>On</th><th></th></tr></thead>
					<tbody>
						{#each caps as cap (cap.id)}
							<tr>
								<td>{scopeLabel(cap)}</td>
								<td>{PERIODS.find((p) => p.value === cap.period)?.label ?? cap.period}</td>
								<td>{UNITS.find((u) => u.value === cap.unit)?.label ?? cap.unit}</td>
								<td class="num">
									{#if canEdit(cap)}
										<Input
											type="number"
											class="ml-auto h-7 w-24 text-right font-mono text-xs"
											value={String(cap.limit)}
											min={0}
											step="any"
											aria-label="Limit"
											disabled={busy}
											onchange={(e) => run(() => updateSpendCap(cap.id, { limit: Number((e.currentTarget as HTMLInputElement).value) }), 'Could not change the cap.')}
										/>
									{:else}
										<span class="font-mono">{cap.unit === 'tokens' ? cap.limit.toLocaleString() : `$${cap.limit.toFixed(2)}`}</span>
									{/if}
								</td>
								<td>{cap.action}</td>
								<td>
									<Switch
										checked={cap.enabled}
										disabled={busy || !canEdit(cap)}
										onCheckedChange={(v) => run(() => updateSpendCap(cap.id, { enabled: v }), 'Could not change the cap.')}
									/>
								</td>
								<td>
									{#if canEdit(cap)}
										<Button variant="ghost" size="icon" aria-label="Delete the cap" disabled={busy} onclick={() => run(() => deleteSpendCap(cap.id), 'Could not delete the cap.')}>
											<Trash2 />
										</Button>
									{/if}
								</td>
							</tr>
						{:else}
							<tr><td colspan="7" class="text-xs text-muted-foreground">No cap applies to you.</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
			<div class="flex flex-wrap items-end gap-3 border-t border-border px-4 py-3">
				{#if isStaff}
					<div class="space-y-1">
						<Label for="cap-scope" class="text-xs">Scope</Label>
						<Select.Simple
							id="cap-scope"
							size="sm"
							value={draft.scope}
							items={[
								{ value: 'user', label: 'Me' },
								{ value: 'model', label: 'A model' },
								{ value: 'endpoint', label: 'An endpoint' },
								{ value: 'global', label: 'Platform' },
							]}
							onValueChange={(v) => (draft = { ...draft, scope: v as SpendCapIn['scope'] })}
						/>
					</div>
					{#if draft.scope === 'model'}
						<div class="space-y-1">
							<Label for="cap-model" class="text-xs">Model id</Label>
							<Input id="cap-model" class="h-8 w-48 font-mono text-xs" bind:value={draft.model_slug} />
						</div>
					{/if}
				{/if}
				<div class="space-y-1">
					<Label for="cap-period" class="text-xs">Period</Label>
					<Select.Simple id="cap-period" size="sm" value={draft.period} items={PERIODS} onValueChange={(v) => (draft = { ...draft, period: v as SpendPeriod })} />
				</div>
				<div class="space-y-1">
					<Label for="cap-unit" class="text-xs">Unit</Label>
					<Select.Simple id="cap-unit" size="sm" value={draft.unit} items={UNITS} onValueChange={(v) => (draft = { ...draft, unit: v as SpendUnit })} />
				</div>
				<NumberField id="cap-limit" label="Limit" value={draft.limit} min={0} integer={draft.unit === 'tokens'} step={draft.unit === 'tokens' ? 1000 : 1} class="w-32" onchange={(v) => (draft = { ...draft, limit: v ?? 0 })} />
				<Button size="sm" disabled={busy || !(draft.limit > 0)} onclick={() => run(() => createSpendCap({ ...draft }), 'Could not add the cap.')}><Plus />Add cap</Button>
			</div>
		{/if}
	</Card.Content>
</Card.Root>
