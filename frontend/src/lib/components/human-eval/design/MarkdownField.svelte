<script lang="ts">
	/**
	 * MarkdownField — a Markdown textarea with a Write / Preview switch. The
	 * preview goes through the sanitizing docs renderer (`renderMarkdown`), never
	 * raw HTML. Read-only fields show the rendered text only.
	 *
	 * Props: `id`, `label`, `value` (bindable), `rows`, `placeholder`, `hint`,
	 * `error`, `readOnly`, `maxlength`.
	 */
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Segmented } from '$lib/components/ui/segmented';
	import { renderMarkdown } from '$lib/docs/markdown';

	interface Props {
		id: string;
		label: string;
		value: string | undefined;
		rows?: number;
		placeholder?: string;
		hint?: string;
		error?: string;
		readOnly?: boolean;
		maxlength?: number;
	}

	let {
		id,
		label,
		value = $bindable(''),
		rows = 5,
		placeholder = '',
		hint = '',
		error = '',
		readOnly = false,
		maxlength = 20000,
	}: Props = $props();

	let view = $state<'write' | 'preview'>('write');
	let html = $state('');
	let rendering = $state(false);
	let seq = 0;

	const showPreview = $derived(readOnly || view === 'preview');

	$effect(() => {
		if (!showPreview) return;
		const source = value ?? '';
		const mine = ++seq;
		rendering = true;
		renderMarkdown(source)
			.then((r) => {
				if (mine === seq) html = r.html;
			})
			.catch(() => {
				if (mine === seq) html = '';
			})
			.finally(() => {
				if (mine === seq) rendering = false;
			});
	});
</script>

<div class="min-w-0 space-y-1.5">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<Label for={readOnly ? undefined : id} class="min-w-0 text-xs">{label}</Label>
		{#if !readOnly}
			<Segmented
				size="sm"
				class="shrink-0"
				aria-label="{label}: write or preview"
				value={view}
				onValueChange={(v) => (view = v === 'preview' ? 'preview' : 'write')}
				items={[
					{ value: 'write', label: 'Write' },
					{ value: 'preview', label: 'Preview' },
				]}
			/>
		{/if}
	</div>
	{#if showPreview}
		<div class="min-h-12 rounded-md border border-border bg-surface-1 px-3 py-2 text-sm" aria-live="polite">
			{#if !(value ?? '').trim()}
				<p class="text-muted-foreground">Nothing written yet.</p>
			{:else if rendering && !html}
				<p class="whitespace-pre-wrap text-muted-foreground">{value}</p>
			{:else}
				<div class="docs-article max-w-none text-sm">{@html html}</div>
			{/if}
		</div>
	{:else}
		<Textarea
			{id}
			bind:value
			{rows}
			{placeholder}
			{maxlength}
			aria-invalid={error ? true : undefined}
			aria-describedby={error || hint ? `${id}-note` : undefined}
			class="font-mono text-[13px]"
		/>
	{/if}
	{#if error}
		<p id="{id}-note" class="text-[11px] text-danger-fg">{error}</p>
	{:else if hint}
		<p id="{id}-note" class="text-[11px] text-muted-foreground">{hint}</p>
	{/if}
</div>
