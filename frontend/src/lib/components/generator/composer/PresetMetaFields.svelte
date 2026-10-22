<script lang="ts" module>
	export const PRESET_SLUG_RE = /^[a-z0-9][a-z0-9-]{1,80}$/;

	/** "Aider: whole files!" → "aider-whole-files". */
	export function slugify(text: string): string {
		return text
			.toLowerCase()
			.normalize('NFKD')
			.replace(/[^a-z0-9]+/g, '-')
			.replace(/^-+|-+$/g, '')
			.slice(0, 80)
			.replace(/-+$/, '');
	}

	export function presetSlugError(slug: string): string {
		if (!slug) return 'Required';
		if (!PRESET_SLUG_RE.test(slug))
			return 'Lowercase letters, digits and dashes; 2–81 characters, starting with a letter or digit';
		return '';
	}
</script>

<script lang="ts">
	/**
	 * PresetMetaFields — name, slug (derived from the name until edited) and
	 * description for a new run preset.
	 *
	 * Props: `name`, `slug`, `description` (all bindable), `idPrefix`,
	 * `slugError` (server message for the slug, e.g. "slug already in use"), `disabled`.
	 */
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';

	interface Props {
		name: string;
		slug: string;
		description: string;
		idPrefix: string;
		slugError?: string;
		disabled?: boolean;
	}

	let {
		name = $bindable(''),
		slug = $bindable(''),
		description = $bindable(''),
		idPrefix,
		slugError = '',
		disabled = false,
	}: Props = $props();

	let slugTouched = $state(false);
	const localSlugError = $derived(slugTouched || slug ? presetSlugError(slug) : '');
	const shownSlugError = $derived(localSlugError || slugError);

	function onName(next: string) {
		name = next;
		if (!slugTouched) slug = slugify(next);
	}
</script>

<div class="grid gap-3 sm:grid-cols-2">
	<div class="min-w-0 space-y-1.5">
		<Label class="text-xs" for="{idPrefix}-name">Name</Label>
		<Input
			id="{idPrefix}-name"
			value={name}
			maxlength={200}
			placeholder="e.g. Aider whole files"
			class="h-8"
			{disabled}
			oninput={(e: Event) => onName((e.currentTarget as HTMLInputElement).value)}
		/>
	</div>
	<div class="min-w-0 space-y-1.5">
		<Label class="text-xs" for="{idPrefix}-slug">Slug</Label>
		<Input
			id="{idPrefix}-slug"
			value={slug}
			maxlength={81}
			placeholder="aider-whole-files"
			class="h-8 font-mono text-xs"
			aria-invalid={shownSlugError ? true : undefined}
			aria-describedby="{idPrefix}-slug-note"
			{disabled}
			oninput={(e: Event) => {
				slugTouched = true;
				slug = (e.currentTarget as HTMLInputElement).value.trim();
			}}
		/>
		<p id="{idPrefix}-slug-note" class="text-[11px] {shownSlugError ? 'text-danger-fg' : 'text-muted-foreground'}">
			{shownSlugError || 'Identifies the preset in packages and run provenance; unique per kind.'}
		</p>
	</div>
	<div class="min-w-0 space-y-1.5 sm:col-span-2">
		<Label class="text-xs" for="{idPrefix}-description">Description <span class="font-normal text-muted-foreground">(optional)</span></Label>
		<Textarea
			id="{idPrefix}-description"
			rows={2}
			bind:value={description}
			placeholder="What this preset changes and why"
			class="min-h-0 text-sm"
			{disabled}
		/>
	</div>
</div>
