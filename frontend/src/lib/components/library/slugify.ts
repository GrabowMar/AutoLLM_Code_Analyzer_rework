/** A library slug from a name: lower-case, `[a-z0-9_-]`, starting with a letter or digit, ≤ 100 chars. */
export function slugify(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^[-_]+|[-_]+$/g, "")
    .slice(0, 100);
}

/** True when `slug` is a valid library slug (the server's `^[A-Za-z0-9][A-Za-z0-9_-]*$`, ≤ 100). */
export function isValidSlug(slug: string): boolean {
  return (
    slug.length > 0 &&
    slug.length <= 100 &&
    /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(slug)
  );
}
