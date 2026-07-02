/** Derive a URL-safe slug from a display name (used by template forms). */
export function slugify(name: string): string {
	return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}
