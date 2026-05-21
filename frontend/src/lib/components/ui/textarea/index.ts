import Root from './textarea.svelte';

export type TextareaProps = {
class?: string;
value?: string;
placeholder?: string;
disabled?: boolean;
rows?: number;
[key: string]: unknown;
};

export { Root as Textarea, Root };
