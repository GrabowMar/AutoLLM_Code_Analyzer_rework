<script lang="ts">
	import { Label } from '$lib/components/ui/label';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import type { LLMParams } from '$lib/api/client';

	interface Props {
		/** Only explicitly set fields are present; empty inputs mean "inherit". */
		params: LLMParams;
		/** Values inherited from the profile/experiment, shown as placeholders. */
		inherited?: LLMParams;
		idPrefix: string;
	}

	let { params = $bindable({}), inherited = {}, idPrefix }: Props = $props();

	let showExpert = $state(false);
	let providerText = $state(params.provider ? JSON.stringify(params.provider, null, 2) : '');
	let providerError = $state('');
	let stopText = $state((params.stop ?? []).join(', '));

	const inputClass =
		'flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30';

	const numberFields: {
		key: keyof LLMParams;
		label: string;
		min: number;
		max: number;
		step: number;
		hint?: string;
	}[] = [
		{ key: 'temperature', label: 'Temperature', min: 0, max: 2, step: 0.1 },
		{ key: 'top_p', label: 'Top P', min: 0, max: 1, step: 0.05 },
		{ key: 'max_tokens', label: 'Max Tokens', min: 1, max: 2000000, step: 1 },
		{ key: 'top_k', label: 'Top K', min: 1, max: 1000, step: 1 },
		{ key: 'min_p', label: 'Min P', min: 0, max: 1, step: 0.01 },
		{ key: 'frequency_penalty', label: 'Frequency Penalty', min: -2, max: 2, step: 0.1 },
		{ key: 'presence_penalty', label: 'Presence Penalty', min: -2, max: 2, step: 0.1 },
		{ key: 'repetition_penalty', label: 'Repetition Penalty', min: 0, max: 2, step: 0.05 },
	];

	function placeholderFor(key: string): string {
		const value = (inherited as Record<string, unknown>)[key];
		return value === undefined || value === null ? 'inherit' : String(value);
	}

	function setNumber(key: keyof LLMParams, raw: string, integer: boolean) {
		if (raw.trim() === '') {
			const { [key]: _removed, ...rest } = params;
			params = rest;
			return;
		}
		const parsed = integer ? parseInt(raw, 10) : parseFloat(raw);
		if (!Number.isNaN(parsed)) {
			params = { ...params, [key]: parsed };
		}
	}

	function setStop(raw: string) {
		stopText = raw;
		const items = raw.split(',').map((s) => s.trim()).filter(Boolean);
		if (items.length === 0) {
			const { stop: _removed, ...rest } = params;
			params = rest;
		} else {
			params = { ...params, stop: items };
		}
	}

	function setResponseFormat(value: string) {
		if (value === '') {
			const { response_format: _removed, ...rest } = params;
			params = rest;
		} else {
			params = { ...params, response_format: { type: value } };
		}
	}

	function setReasoningEffort(value: string) {
		if (value === '') {
			const { reasoning: _removed, ...rest } = params;
			params = rest;
		} else {
			params = { ...params, reasoning: { effort: value } };
		}
	}

	function setProvider(raw: string) {
		providerText = raw;
		providerError = '';
		if (raw.trim() === '') {
			const { provider: _removed, ...rest } = params;
			params = rest;
			return;
		}
		try {
			const parsed = JSON.parse(raw);
			if (typeof parsed !== 'object' || Array.isArray(parsed) || parsed === null) {
				providerError = 'Must be a JSON object';
				return;
			}
			params = { ...params, provider: parsed };
		} catch {
			providerError = 'Invalid JSON';
		}
	}
</script>

<div class="space-y-3 rounded-md border bg-muted/20 p-3">
	<p class="text-[10px] text-muted-foreground">
		Blank fields inherit from the profile (or the system defaults). Set values are frozen into the run snapshot.
	</p>

	<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
		{#each numberFields as field}
			<div class="space-y-1.5">
				<Label class="text-xs" for="{idPrefix}-{field.key}">{field.label}</Label>
				<input
					id="{idPrefix}-{field.key}"
					type="number"
					min={field.min}
					max={field.max}
					step={field.step}
					value={params[field.key] ?? ''}
					placeholder={placeholderFor(field.key)}
					oninput={(e) => setNumber(field.key, e.currentTarget.value, field.step === 1)}
					class={inputClass}
				/>
			</div>
		{/each}
	</div>

	<div class="space-y-2">
		<button
			type="button"
			class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
			onclick={() => (showExpert = !showExpert)}
		>
			<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showExpert ? 'rotate-90' : ''}" />
			Expert (stop, response format, routing, reasoning)
		</button>

		{#if showExpert}
			<div class="grid gap-3 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label class="text-xs" for="{idPrefix}-stop">Stop Sequences</Label>
					<input
						id="{idPrefix}-stop"
						type="text"
						value={stopText}
						placeholder="comma-separated, max 8"
						oninput={(e) => setStop(e.currentTarget.value)}
						class={inputClass}
					/>
				</div>
				<div class="space-y-1.5">
					<Label class="text-xs" for="{idPrefix}-response-format">Response Format</Label>
					<select
						id="{idPrefix}-response-format"
						value={(params.response_format?.type as string) ?? ''}
						onchange={(e) => setResponseFormat(e.currentTarget.value)}
						class={inputClass}
					>
						<option value="">inherit / free text</option>
						<option value="json_object">JSON object</option>
					</select>
				</div>
				<div class="space-y-1.5">
					<Label class="text-xs" for="{idPrefix}-reasoning">Reasoning Effort</Label>
					<select
						id="{idPrefix}-reasoning"
						value={(params.reasoning?.effort as string) ?? ''}
						onchange={(e) => setReasoningEffort(e.currentTarget.value)}
						class={inputClass}
					>
						<option value="">inherit / model default</option>
						<option value="low">low</option>
						<option value="medium">medium</option>
						<option value="high">high</option>
					</select>
				</div>
				<div class="space-y-1.5">
					<Label class="text-xs" for="{idPrefix}-provider">Provider Routing (JSON)</Label>
					<textarea
						id="{idPrefix}-provider"
						rows="3"
						value={providerText}
						placeholder={'{"order": ["anthropic"], "allow_fallbacks": true}'}
						oninput={(e) => setProvider(e.currentTarget.value)}
						class="{inputClass} h-auto font-mono text-xs"
					></textarea>
					{#if providerError}
						<p class="text-[10px] text-destructive">{providerError}</p>
					{/if}
				</div>
			</div>
		{/if}
	</div>
</div>
