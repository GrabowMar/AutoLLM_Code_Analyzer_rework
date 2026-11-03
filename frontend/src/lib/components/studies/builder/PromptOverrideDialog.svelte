<script lang="ts" module>
	import type { SourceSpanOut } from '$lib/api/v3/types';

	/** What the researcher edits: the template behind a source span, or one role of a prompt-set level. */
	export type OverrideTarget =
		| { kind: 'span'; span: SourceSpanOut; role: string | null }
		| { kind: 'prompt_set'; ref: string };
</script>

<script lang="ts">
	/**
	 * PromptOverrideDialog — edit the text behind a stimulus (spec D10, 9.4 prompt-overrides). The edited
	 * text is the whole PromptTemplate that rendered it; saving forks the owning entity as a user version
	 * (library entities are immutable), swaps the study's factor level to the fork and records the diff.
	 * The plan then re-resolves (a frozen plan becomes stale).
	 *
	 * Props: `open`, `studyId` (overrides need a saved draft), `target`, `onclose()`, `onapplied(override)`.
	 */
	import { createPromptOverride } from '$lib/api/v3/studies';
	import { getLibraryVersion } from '$lib/api/v3/library';
	import type { PromptSetContent, PromptTemplateContent, StudyPromptOverrideOut } from '$lib/api/v3/types';
	import { parseRef } from '$lib/api/v3/types';
	import { formatApiError } from '$lib/api/core';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import * as Select from '$lib/components/ui/select';
	import { Label } from '$lib/components/ui/label';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { PinnedRefBadge, PromptTemplateEditor, invalidateLibrary } from '$lib/components/library';
	import { buildOverrideRequest, OWNER_LABELS } from './spans';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		open: boolean;
		studyId: string | null;
		target: OverrideTarget | null;
		onclose: () => void;
		onapplied: (override: StudyPromptOverrideOut) => void;
	}

	let { open, studyId, target, onclose, onapplied }: Props = $props();

	let loading = $state(false);
	let error = $state('');
	let saveError = $state('');
	let saving = $state(false);
	let roles = $state<{ role: string; ref: string }[]>([]);
	let role = $state('');
	let templateRef = $state('');
	let base = $state<PromptTemplateContent | null>(null);
	let draft = $state<PromptTemplateContent | null>(null);

	async function loadTemplate(ref: string) {
		const parsed = parseRef(ref);
		if (!parsed) {
			error = `Not a template ref: ${ref}`;
			return;
		}
		loading = true;
		error = '';
		try {
			const item = await getLibraryVersion('prompt', parsed.slug, parsed.version);
			templateRef = item.pinned_ref;
			base = item.content as PromptTemplateContent;
			draft = structuredClone(base);
		} catch (e) {
			error = formatApiError(e, 'Could not load the template.');
		} finally {
			loading = false;
		}
	}

	async function loadPromptSet(ref: string) {
		const parsed = parseRef(ref);
		if (!parsed) return;
		loading = true;
		error = '';
		try {
			const item = await getLibraryVersion('prompt_set', parsed.slug, parsed.version);
			const content = item.content as PromptSetContent;
			roles = Object.entries(content.roles ?? {})
				.filter(([, r]) => !!r)
				.map(([r, tref]) => ({ role: r, ref: tref as string }));
			role = roles.find((r) => r.role === 'user')?.role ?? roles[0]?.role ?? '';
			if (role) await loadTemplate(roles.find((r) => r.role === role)!.ref);
		} catch (e) {
			error = formatApiError(e, 'Could not load the prompt set.');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		if (!open || !target) return;
		base = null;
		draft = null;
		saveError = '';
		roles = [];
		if (target.kind === 'span') {
			role = target.role ?? '';
			void loadTemplate(target.span.template_ref);
		} else {
			void loadPromptSet(target.ref);
		}
	});

	const owner = $derived(
		target?.kind === 'span'
			? { owner_kind: target.span.owner_kind, owner_ref: target.span.owner_ref }
			: templateRef
				? { owner_kind: 'prompt' as const, owner_ref: templateRef }
				: null,
	);
	const request = $derived(owner && base && draft ? buildOverrideRequest(owner, role || null, base.template, draft.template) : null);

	async function apply() {
		if (!studyId || !request) return;
		saving = true;
		saveError = '';
		try {
			const out = await createPromptOverride(studyId, request);
			invalidateLibrary();
			onapplied(out);
			onclose();
		} catch (e) {
			saveError = formatApiError(e, 'Could not apply the edit.');
		} finally {
			saving = false;
		}
	}
</script>

<Dialog.Root {open} onOpenChange={(v) => !v && onclose()}>
	<Dialog.Content class="max-h-[90dvh] max-w-3xl overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>Edit prompt text</Dialog.Title>
			<Dialog.Description>
				Saving forks the owner as your own version and switches this study to it. Library rows never change in place.
			</Dialog.Description>
		</Dialog.Header>
		<div class="space-y-3">
			{#if target?.kind === 'span'}
				<p class="flex flex-wrap items-center gap-2 text-xs">
					<span class="text-muted-foreground">{OWNER_LABELS[target.span.owner_kind]}</span>
					<span class="font-mono">{target.span.owner_ref.split('#')[0]}</span>
					<span class="text-muted-foreground">rendered by</span>
					<PinnedRefBadge ref={target.span.template_ref} kind="prompt" />
				</p>
			{:else if roles.length > 0}
				<div class="space-y-1.5">
					<Label for="override-role">Role</Label>
					<Select.Simple
						id="override-role"
						size="sm"
						value={role}
						items={roles.map((r) => ({ value: r.role, label: r.role }))}
						onValueChange={(v) => {
							role = v;
							const r = roles.find((x) => x.role === v);
							if (r) void loadTemplate(r.ref);
						}}
					/>
				</div>
			{/if}
			{#if !studyId}
				<p class="text-xs text-warning-fg">The draft is saved first: complete the required steps, then edit.</p>
			{/if}
			{#if error}
				<ErrorAlert message={error} />
			{:else if loading || !draft}
				<LoadingState label="loading the template" />
			{:else}
				<PromptTemplateEditor value={draft} {base} onchange={(v) => (draft = v)} />
			{/if}
			{#if saveError}<ErrorAlert message={saveError} />{/if}
		</div>
		<Dialog.Footer class="mt-4 flex justify-end gap-2">
			<Button variant="outline" onclick={onclose}>Cancel</Button>
			<Button onclick={apply} disabled={!request || saving || !studyId}>
				{#if saving}<LoaderCircle class="animate-spin" />{/if}Save as my version
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
