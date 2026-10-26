<script lang="ts">
	import type { CredentialStatus } from '$lib/api/credentials';
	import SettingsBlock from './SettingsBlock.svelte';
	import CredentialRow from './CredentialRow.svelte';
	import { useSectionStatus } from './section-status.svelte';

	const report = useSectionStatus('credentials');

	let openrouter = $state<CredentialStatus | null | undefined>(undefined);

	// The rail shows the key generation depends on: OpenRouter.
	$effect(() => {
		if (openrouter === undefined) return;
		if (!openrouter?.configured) report({ tone: 'warning', label: 'no key' });
		else if (openrouter.last_validation_status === 'valid') report({ tone: 'success', label: 'valid' });
		else if (openrouter.last_validation_status === 'invalid') report({ tone: 'danger', label: 'invalid' });
		else report({ tone: 'neutral', label: 'unchecked' });
	});
</script>

<SettingsBlock>
	<CredentialRow
		provider="openrouter"
		title="OpenRouter"
		description="Runs the generation jobs and experiments you submit; never shared with other users. Get one at"
		signupUrl="https://openrouter.ai/keys"
		signupLabel="openrouter.ai/keys"
		placeholder="sk-or-v1-…"
		inputId="openrouter-key"
		onstatus={(s) => (openrouter = s)}
	/>
	<CredentialRow
		provider="huggingface"
		title="Hugging Face"
		description="Model metadata, downloads and rankings sync. Get one at"
		signupUrl="https://huggingface.co/settings/tokens"
		signupLabel="huggingface.co/settings/tokens"
		placeholder="hf_…"
		inputId="huggingface-key"
	/>
</SettingsBlock>

<p class="settings-row text-[13px] leading-5 text-muted-foreground">
	Other cloud providers (Anthropic, OpenAI, Groq…) connect as endpoints under
	<a href="#providers" class="settings-link">LLM providers</a>. The GitHub token lives under
	<a href="#github" class="settings-link">GitHub</a>.
</p>
