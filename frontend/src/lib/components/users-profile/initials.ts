/** Two-letter avatar initials from a display name, falling back to the email. */
export function initialsFor(
  name: string | null | undefined,
  email: string,
): string {
  const source = name?.trim() || email;
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return source.slice(0, 2).toUpperCase();
}
