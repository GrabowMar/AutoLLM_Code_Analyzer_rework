<script lang="ts">
	/**
	 * Generic, recursive renderer for report payloads without a typed view
	 * (legacy schema v1, unknown types, unknown sections). Structure becomes
	 * markup, never JSON text:
	 * - a list of objects → a table (nested values rendered in the cells);
	 * - an object whose values are statistics (`{mean, n, …}`) → a stats table;
	 * - an object whose values are objects (`{cost: {a, b, delta}, …}`) → a
	 *   table keyed by the outer key;
	 * - other objects → a definition grid plus sub-sections.
	 * `null` renders "—" (missing), never 0.
	 */
	import Self from './GenericData.svelte';
	import StatTable from './StatTable.svelte';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import { fmtNum, humanize } from '../format';
	import type { ReportStatValue } from '$lib/api/reports';

	interface Props {
		value: unknown;
		/** Nesting depth (limits headings and recursion). */
		depth?: number;
		/** Rendered inside a table cell: keep it compact. */
		compact?: boolean;
	}

	let { value, depth = 0, compact = false }: Props = $props();

	const MAX_DEPTH = 6;
	const MAX_COLUMNS = 14;

	type Obj = Record<string, unknown>;

	function isObj(v: unknown): v is Obj {
		return typeof v === 'object' && v !== null && !Array.isArray(v);
	}
	function isPrimitive(v: unknown): boolean {
		return v === null || v === undefined || ['string', 'number', 'boolean'].includes(typeof v);
	}
	function isStat(v: unknown): v is ReportStatValue {
		return isObj(v) && 'n' in v && ('mean' in v || 'share_true' in v);
	}
	function columnsOf(rows: Obj[]): string[] {
		const cols: string[] = [];
		for (const r of rows) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
		return cols.slice(0, MAX_COLUMNS);
	}
	function fmtPrimitive(v: unknown): string {
		if (typeof v === 'number')
			return Number.isInteger(v) ? v.toLocaleString() : (fmtNum(v, 4) ?? '');
		if (typeof v === 'boolean') return v ? 'yes' : 'no';
		return String(v);
	}

	const kind = $derived.by(() => {
		const v = value;
		if (v === null || v === undefined) return 'missing';
		if (isPrimitive(v)) return 'primitive';
		if (depth >= MAX_DEPTH) return 'deep';
		if (Array.isArray(v)) {
			if (v.length === 0) return 'empty';
			if (v.every(isPrimitive)) return 'list';
			if (v.every(isObj)) return 'rows';
			return 'mixed';
		}
		const o = v as Obj;
		const vals = Object.values(o);
		if (vals.length === 0) return 'empty';
		if (isStat(o)) return 'stat';
		if (vals.every((x) => isStat(x) || x === null)) return 'stats';
		const flatObj = (x: unknown) =>
			isObj(x) && Object.values(x).every((y) => isPrimitive(y) || isStat(y));
		if (vals.some(flatObj) && vals.every((x) => x === null || flatObj(x))) return 'keyed';
		return 'object';
	});

	const statRows = $derived(
		kind === 'stats'
			? Object.entries(value as Obj).map(([k, s]) => ({
					label: humanize(k),
					stat: s as ReportStatValue | null
				}))
			: []
	);
	const keyedRows = $derived(
		kind === 'keyed'
			? Object.entries(value as Obj).map(([k, row]) =>
					row === null ? { __key: k, __null: true } : { __key: k, ...(row as Obj) }
				)
			: []
	);
	const entries = $derived(kind === 'object' ? Object.entries(value as Obj) : []);
	const flatEntries = $derived(entries.filter(([, v]) => isPrimitive(v) || isStat(v)));
	const nestedEntries = $derived(entries.filter(([, v]) => !isPrimitive(v) && !isStat(v)));
</script>

{#snippet statInline(s: Obj)}
	{#if 'share_true' in s}
		{#if s.share_true == null}<NaValue reason="No observation (n=0)" />{:else}<span
				class="font-mono tabular-nums">{fmtNum((s.share_true as number) * 100, 1)}%</span
			>{/if}
	{:else if s.mean == null}
		<NaValue reason="No observation (n=0)" />
	{:else}
		<span class="font-mono tabular-nums">{fmtNum(s.mean as number, 3)}</span>
		{#if s.stdev != null}<span class="text-muted-foreground">
				±{fmtNum(s.stdev as number, 3)}</span
			>{/if}
	{/if}
	<span class="text-[11px] text-muted-foreground"> n={String(s.n)}</span>
{/snippet}

{#snippet table(rows: Obj[], keyColumn: string | null)}
	{@const cols = columnsOf(rows).filter((c) => c !== '__key' && c !== '__null')}
	<div class="table-scroll-wrapper">
		<table class="text-xs">
			<thead>
				<tr>
					{#if keyColumn}<th scope="col">{keyColumn}</th>{/if}
					{#each cols as c (c)}<th scope="col" class="capitalize">{humanize(c)}</th>{/each}
				</tr>
			</thead>
			<tbody>
				{#each rows as r, i (i)}
					<tr>
						{#if keyColumn}<td class="font-medium">{humanize(String(r.__key))}</td>{/if}
						{#each cols as c (c)}
							<td
								class="align-top {typeof r[c] === 'number'
									? 'text-right font-mono tabular-nums'
									: ''}"
							>
								{#if r.__null}
									<NaValue reason="No value (null): nothing to compare or measure for this key" />
								{:else if !(c in r)}
									<NaValue reason="Not present in this row" />
								{:else}
									<Self value={r[c]} depth={depth + 1} compact />
								{/if}
							</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/snippet}

{#if kind === 'missing'}
	<NaValue reason="Not reported (null)" />
{:else if kind === 'primitive'}
	<span class={typeof value === 'number' ? 'font-mono tabular-nums' : 'break-words'}
		>{fmtPrimitive(value)}</span
	>
{:else if kind === 'empty'}
	<span class="text-muted-foreground">none</span>
{:else if kind === 'list'}
	<span class="break-words"
		>{(value as unknown[]).map((v) => (v == null ? '—' : fmtPrimitive(v))).join(', ')}</span
	>
{:else if kind === 'stat'}
	{@render statInline(value as Obj)}
{:else if kind === 'stats'}
	<StatTable rows={statRows} />
{:else if kind === 'rows'}
	{@render table(value as Obj[], null)}
{:else if kind === 'keyed'}
	{@render table(keyedRows, 'Key')}
{:else if kind === 'mixed'}
	<ul class="list-disc space-y-1 pl-4">
		{#each value as unknown[] as item, i (i)}
			<li><Self value={item} depth={depth + 1} compact /></li>
		{/each}
	</ul>
{:else if kind === 'deep'}
	<span class="text-muted-foreground">(nested too deeply to display — see the JSON download)</span>
{:else}
	<div class="space-y-3">
		{#if flatEntries.length > 0}
			<dl class={compact ? 'space-y-0.5' : 'grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4'}>
				{#each flatEntries as [k, v] (k)}
					<div class={compact ? 'flex gap-1.5' : 'min-w-0'}>
						<dt
							class={compact
								? 'text-muted-foreground'
								: 'text-[11px] text-muted-foreground capitalize'}
						>
							{humanize(k)}{compact ? ':' : ''}
						</dt>
						<dd class={compact ? '' : 'text-sm font-medium'}>
							{#if isStat(v)}{@render statInline(v as unknown as Obj)}{:else}<Self
									value={v}
									depth={depth + 1}
									compact
								/>{/if}
						</dd>
					</div>
				{/each}
			</dl>
		{/if}
		{#each nestedEntries as [k, v] (k)}
			<div class={compact ? '' : 'space-y-2 border-t pt-3'}>
				<p class={compact ? 'text-muted-foreground' : 'section-label'}>{humanize(k)}</p>
				<Self value={v} depth={depth + 1} {compact} />
			</div>
		{/each}
	</div>
{/if}
