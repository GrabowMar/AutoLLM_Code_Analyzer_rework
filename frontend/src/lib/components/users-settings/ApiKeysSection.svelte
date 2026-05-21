<script lang="ts">
import CredentialCard from './CredentialCard.svelte';
</script>

<div class="space-y-6">
<CredentialCard
provider="openrouter"
title="OpenRouter API Key"
description="Your personal OpenRouter API key is used for generation jobs you submit. Never shared with other users."
signupUrl="https://openrouter.ai/keys"
signupLabel="openrouter.ai/keys"
placeholder="sk-or-v1-..."
inputId="openrouter-key"
/>

<CredentialCard
provider="huggingface"
title="Hugging Face Token"
description="Personal Hugging Face access token used for model metadata, downloads, and rankings sync."
signupUrl="https://huggingface.co/settings/tokens"
signupLabel="huggingface.co/settings/tokens"
placeholder="hf_..."
inputId="huggingface-key"
/>
</div>
