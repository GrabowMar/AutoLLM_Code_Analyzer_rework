<script lang="ts">
	/**
	 * ReportCellView — one typed report table cell, rendered by column kind:
	 * int / float / pct / pct_ci / p / ci / usd / bool / hash / list /
	 * status / code / text. `null` is missing data: "—" with the reason
	 * (the row's `note` when it has one), never 0. Ids link to their run or
	 * application (provenance); significant adjusted p-values are marked.
	 *
	 * Props: `column`, `row`, `prov` (provenance index).
	 */
	import type { ReportCell, ReportColumn, ReportRow } from '$lib/api/analysis';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { groundingTones } from '$lib/constants/colors';
	import Check from '@lucide/svelte/icons/check';
	import Minus from '@lucide/svelte/icons/minus';
	import NaValue from '../../NaValue.svelte';
	import {
		NA_REASON,
		deviationTones,
		fmtDateTime,
		fmtFloat,
		fmtInt,
		fmtInterval,
		fmtP,
		fmtPct,
		fmtUsd,
		shortHash
	} from '../format';
	import { idHref, labelHref, type ReportProvenance } from './provenance';

	interface Props {
		column: ReportColumn;
		row: ReportRow;
		prov: ReportProvenance;
	}

	let { column, row, prov }: Props = $props();

	const value = $derived(row[column.key] as ReportCell | undefined);

	const naReason = $derived.by(() => {
		const note = typeof row.note === 'string' && row.note ? row.note : '';
		if (note) return `Not available: ${note}`;
		switch (column.kind) {
			case 'p':
				return 'Not computable (too few observations, or no variation). Never read as 0.';
			case 'ci':
			case 'pct_ci':
				return 'No interval: too few observations, or intervals are computed for the primary outcome only.';
			default:
				return NA_REASON;
		}
	});

	const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

	const significant = $derived(column.key === 'p_adj' && row.significant === true);

	function isNum(v: unknown): v is number {
		return typeof v === 'number' && Number.isFinite(v);
	}

	function codeText(v: ReportCell): string {
		if (v === null) return '';
		if (typeof v === 'object') return JSON.stringify(v, null, 1);
		return String(v);
	}
</script>

{#if value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value))}
	<NaValue reason={naReason} />
{:else if column.kind === 'int'}
	<span class="tabular-nums">{isNum(value) ? fmtInt(value) : String(value)}</span>
{:else if column.kind === 'float'}
	<span class="tabular-nums" title={isNum(value) ? String(value) : undefined}
		>{isNum(value) ? fmtFloat(value) : String(value)}</span
	>
{:else if column.kind === 'pct'}
	<span class="tabular-nums" title={isNum(value) ? String(value) : undefined}
		>{isNum(value) ? fmtPct(value) : String(value)}</span
	>
{:else if column.kind === 'usd'}
	<span class="tabular-nums" title={isNum(value) ? `$${value}` : undefined}
		>{isNum(value) ? fmtUsd(value) : String(value)}</span
	>
{:else if column.kind === 'p'}
	<span
		class="tabular-nums"
		class:font-semibold={significant}
		title={isNum(value) ? String(value) : undefined}
	>
		{isNum(value) ? fmtP(value) : String(value)}
		{#if significant}<span class="text-success-fg" aria-label="significant">*</span>{/if}
	</span>
{:else if column.kind === 'ci'}
	{@const text = fmtInterval(value)}
	{#if text === '—'}<NaValue reason={naReason} />{:else}<span class="whitespace-nowrap tabular-nums"
			>{text}</span
		>{/if}
{:else if column.kind === 'pct_ci'}
	{@const text = fmtInterval(value, (n) => fmtPct(n))}
	{#if text === '—'}<NaValue reason={naReason} />{:else}<span class="whitespace-nowrap tabular-nums"
			>{text}</span
		>{/if}
{:else if column.kind === 'bool'}
	{#if value === true}
		<span class="inline-flex items-center gap-1 text-success-fg"
			><Check class="h-3.5 w-3.5" aria-hidden="true" />Yes</span
		>
	{:else if value === false}
		<span class="inline-flex items-center gap-1 text-muted-foreground"
			><Minus class="h-3.5 w-3.5" aria-hidden="true" />No</span
		>
	{:else}
		{String(value)}
	{/if}
{:else if column.kind === 'hash'}
	{@const full = String(value)}
	{@const href = idHref(prov, column.key, full)}
	{#if href}
		<a
			{href}
			class="font-mono text-xs text-foreground hover:text-primary hover:underline"
			title={full}>{shortHash(full, 8)}</a
		>
	{:else}
		<code class="font-mono text-xs" title={full}>{shortHash(full)}</code>
	{/if}
{:else if column.kind === 'list'}
	{#if Array.isArray(value) && value.length === 0}
		<span class="text-muted-foreground" title="Empty list"
			>{column.key === 'observed' ? 'not recorded' : 'none'}</span
		>
	{:else if Array.isArray(value)}
		<span class="flex max-w-[28rem] flex-wrap gap-1">
			{#each value as item, i (i)}
				{@const text =
					typeof item === 'object' && item !== null ? JSON.stringify(item) : String(item)}
				{@const href = typeof item === 'string' ? labelHref(prov, item) : null}
				{#if href}
					<a
						{href}
						class="mono-tag hover:border-primary/50 hover:text-primary"
						title="Open the run of this target">{text}</a
					>
				{:else}
					<span class="mono-tag max-w-full truncate" title={text}>{text}</span>
				{/if}
			{/each}
		</span>
	{:else}
		{String(value)}
	{/if}
{:else if column.kind === 'status'}
	<StatusBadge status={String(value)} tones={deviationTones} />
{:else if column.key === 'grounding' && typeof value === 'string'}
	<StatusBadge status={value} tones={groundingTones} />
{:else if column.kind === 'code'}
	{#if typeof value === 'object'}
		<pre
			class="max-w-[32rem] overflow-x-auto whitespace-pre-wrap break-all font-mono text-[11px] text-muted-foreground">{codeText(
				value
			)}</pre>
	{:else if value === ''}
		<span class="text-muted-foreground" title="Empty value">(empty)</span>
	{:else}
		{@const text = String(value)}
		{@const href = typeof value === 'string' ? idHref(prov, String(row.key ?? ''), text) : null}
		{#if href}
			<a {href} class="break-all font-mono text-xs hover:text-primary hover:underline">{text}</a>
		{:else}
			<code class="break-all font-mono text-xs">{text}</code>
		{/if}
	{/if}
{:else if isNum(value)}
	<span class="tabular-nums">{fmtFloat(value)}</span>
{:else if typeof value === 'boolean'}
	{value ? 'Yes' : 'No'}
{:else if typeof value === 'object'}
	<code class="font-mono text-[11px]">{JSON.stringify(value)}</code>
{:else if value === ''}
	<span class="text-muted-foreground" title="Empty value">(empty)</span>
{:else if ISO_RE.test(value)}
	<span class="whitespace-nowrap" title={value}>{fmtDateTime(value)}</span>
{:else}
	<span class="break-words">{value}</span>
{/if}
