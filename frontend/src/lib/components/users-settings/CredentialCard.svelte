<script lang="ts">
import {
getCredentialStatus,
setCredentialKey,
deleteCredentialKey,
testCredentialKey,
type CredentialProvider,
type CredentialStatus,
} from '$lib/api/credentials';
import * as Card from '$lib/components/ui/card';
import { Input } from '$lib/components/ui/input';
import { Label } from '$lib/components/ui/label';
import { Button } from '$lib/components/ui/button';
import { Alert, AlertDescription } from '$lib/components/ui/alert';
import { onMount } from 'svelte';
import { toast } from 'svelte-sonner';
import KeyRound from '@lucide/svelte/icons/key-round';
import Trash2 from '@lucide/svelte/icons/trash-2';
import CheckCircle2 from '@lucide/svelte/icons/check-circle-2';
import XCircle from '@lucide/svelte/icons/x-circle';
import AlertTriangle from '@lucide/svelte/icons/triangle-alert';

interface Props {
provider: CredentialProvider;
title: string;
description: string;
signupUrl: string;
signupLabel: string;
placeholder: string;
inputId: string;
}

let { provider, title, description, signupUrl, signupLabel, placeholder, inputId }: Props =
$props();

let status = $state<CredentialStatus | null>(null);
let loading = $state(true);
let saving = $state(false);
let testing = $state(false);
let showForm = $state(false);
let newKey = $state('');

onMount(() => {
refresh();
});

async function refresh() {
loading = true;
try {
status = await getCredentialStatus(provider);
} catch (err) {
console.error(err);
} finally {
loading = false;
}
}

async function handleSave() {
const value = newKey.trim();
if (!value) {
toast.error('Enter an API key.');
return;
}
saving = true;
try {
status = await setCredentialKey(provider, value);
toast.success('API key saved and validated.');
newKey = '';
showForm = false;
} catch (err) {
const detail =
(err as { detail?: string })?.detail ?? 'Could not save key (it may be invalid).';
toast.error(detail);
} finally {
saving = false;
}
}

async function handleDelete() {
if (!confirm(`Remove your stored ${title} API key?`)) return;
saving = true;
try {
status = await deleteCredentialKey(provider);
toast.success('API key removed.');
} catch (err) {
console.error(err);
toast.error('Failed to remove key.');
} finally {
saving = false;
}
}

async function handleTest() {
testing = true;
try {
const result = await testCredentialKey(provider);
if (result.is_valid) toast.success(result.message);
else toast.error(result.message);
await refresh();
} catch (err) {
console.error(err);
toast.error('Could not test key.');
} finally {
testing = false;
}
}

function statusLabel(s: string): string {
switch (s) {
case 'valid':
return 'Valid';
case 'invalid':
return 'Invalid';
case 'rate_limited':
return 'Rate limited';
case 'network_error':
return 'Network error';
default:
return 'Not validated';
}
}

function formatTimestamp(iso: string | null): string {
if (!iso) return 'never';
try {
return new Date(iso).toLocaleString();
} catch {
return iso;
}
}
</script>

<Card.Root>
<Card.Header>
<Card.Title class="flex items-center gap-2">
<KeyRound class="h-5 w-5" />
{title}
</Card.Title>
<Card.Description>
{description}
Get a key at
<a href={signupUrl} target="_blank" rel="noopener" class="underline">{signupLabel}</a>.
</Card.Description>
</Card.Header>
<Card.Content class="space-y-4">
{#if loading}
<div class="h-20 animate-pulse rounded-md bg-muted"></div>
{:else if status?.configured}
<div class="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3">
<div class="space-y-1">
<p class="font-mono text-sm">{status.key_prefix}…</p>
<p class="flex items-center gap-2 text-xs text-muted-foreground">
{#if status.last_validation_status === 'valid'}
<CheckCircle2 class="h-3.5 w-3.5 text-green-500" />
{:else if status.last_validation_status === 'invalid'}
<XCircle class="h-3.5 w-3.5 text-red-500" />
{:else}
<AlertTriangle class="h-3.5 w-3.5 text-amber-500" />
{/if}
<span>{statusLabel(status.last_validation_status)}</span>
<span>· Last checked: {formatTimestamp(status.last_validated_at)}</span>
</p>
{#if status.last_validation_message}
<p class="text-xs text-muted-foreground">{status.last_validation_message}</p>
{/if}
</div>
<div class="flex gap-2">
<Button variant="outline" size="sm" onclick={handleTest} disabled={testing}>
{testing ? 'Testing…' : 'Test'}
</Button>
<Button variant="outline" size="sm" onclick={() => (showForm = !showForm)} disabled={saving}>
{showForm ? 'Cancel' : 'Replace'}
</Button>
<Button variant="destructive" size="sm" onclick={handleDelete} disabled={saving}>
<Trash2 class="h-4 w-4" />
</Button>
</div>
</div>
{:else}
<Alert>
<AlertTriangle class="h-4 w-4" />
<AlertDescription>
{#if status?.global_fallback_available}
No personal key configured. Jobs will use the deployment-wide fallback key if
available, but a personal key is recommended.
{:else}
No API key configured. Add one below.
{/if}
</AlertDescription>
</Alert>
{/if}

{#if showForm || !status?.configured}
<div class="space-y-2">
<Label for={inputId}>{title}</Label>
<Input
id={inputId}
type="password"
{placeholder}
bind:value={newKey}
disabled={saving}
autocomplete="off"
/>
<p class="text-xs text-muted-foreground">
The key is validated against {title.replace(' API Key', '')} before being stored. It is
encrypted at rest and never returned by the API.
</p>
<div class="flex gap-2">
<Button onclick={handleSave} disabled={saving || !newKey.trim()}>
{saving ? 'Validating…' : 'Save & Validate'}
</Button>
{#if status?.configured}
<Button variant="ghost" onclick={() => { showForm = false; newKey = ''; }}>
Cancel
</Button>
{/if}
</div>
</div>
{/if}
</Card.Content>
</Card.Root>
