<script lang="ts">
	/**
	 * JsonContentEditor — the raw content of any library entity as JSON: the escape hatch next to the
	 * structured editors ("Raw JSON") and the fallback for fields a form does not cover. Emits only JSON
	 * that parses to an object; `invalid` reports a parse error to the caller.
	 *
	 * Props: `value`, `onchange`, `readonly`, `oninvalid(message | null)`.
	 */
	import { untrack } from 'svelte';
	import { jsonEqual } from './policy';

	interface Props {
		value: unknown;
		onchange: (v: Record<string, unknown>) => void;
		readonly?: boolean;
		oninvalid?: (message: string | null) => void;
	}

	let { value, onchange, readonly = false, oninvalid }: Props = $props();

	let text = $state('');
	let error = $state<string | null>(null);

	$effect.pre(() => {
		const next = value;
		untrack(() => {
			let current: unknown = undefined;
			try {
				current = JSON.parse(text);
			} catch {
				current = undefined;
			}
			if (!jsonEqual(current, next)) {
				text = JSON.stringify(next, null, 2);
				error = null;
			}
		});
	});

	function input() {
		try {
			const parsed = JSON.parse(text);
			if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('The content must be a JSON object.');
			error = null;
			oninvalid?.(null);
			onchange(parsed as Record<string, unknown>);
		} catch (e) {
			error = (e as Error).message;
			oninvalid?.(error);
		}
	}
</script>

<div class="space-y-1">
	<textarea
		bind:value={text}
		oninput={input}
		readonly={readonly}
		rows={24}
		spellcheck="false"
		aria-label="Content (JSON)"
		aria-invalid={error ? true : undefined}
		class="block w-full rounded-md border border-input bg-surface-1 px-3 py-2 font-mono text-xs leading-relaxed read-only:bg-muted/30 aria-invalid:border-destructive"
	></textarea>
	{#if error}<p class="text-xs text-danger-fg">{error}</p>{/if}
</div>
