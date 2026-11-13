<script lang="ts">
	/**
	 * ResolvedCellDrawer — the builder's cell inspector (Review step) (spec 10.2): one cell of the (possibly ephemeral)
	 * plan in a side sheet, via `CellDetailView`. Works for unsaved plans through the server's resolve
	 * cache.
	 *
	 * Props (contract 10.8): `studyId`, `planHash`, `cellKey`, `open`, `onclose()`; plus `compareKeys` and
	 * `onedit(span, role)` (opens the override dialog).
	 */
	import * as Sheet from '$lib/components/ui/sheet';
	import type { SourceSpanOut } from '$lib/api/v3/types';
	import CellDetailView from './CellDetailView.svelte';

	interface Props {
		studyId: string;
		planHash: string;
		cellKey: string;
		open: boolean;
		onclose: () => void;
		compareKeys?: string[];
		onedit?: (span: SourceSpanOut, role: string) => void;
	}

	let { studyId, planHash, cellKey, open, onclose, compareKeys = [], onedit }: Props = $props();
</script>

<Sheet.Root {open} onOpenChange={(v) => !v && onclose()}>
	<Sheet.Content side="right" class="w-full max-w-3xl overflow-y-auto sm:max-w-3xl">
		<Sheet.Header>
			<Sheet.Title>Cell <span class="font-mono">{cellKey}</span></Sheet.Title>
			<Sheet.Description>
				Everything this sample will be generated from, pinned. <a class="underline" href="/experiments/{studyId}/cells/{cellKey}?plan={planHash}">Open as a page</a>
			</Sheet.Description>
		</Sheet.Header>
		<div class="px-4 pb-6">
			{#if open && cellKey}
				<CellDetailView {studyId} {planHash} {cellKey} {compareKeys} {onedit} />
			{/if}
		</div>
	</Sheet.Content>
</Sheet.Root>
