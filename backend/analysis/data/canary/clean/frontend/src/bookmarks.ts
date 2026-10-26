// CANARY FIXTURE: idiomatic TypeScript module (analyzer negative control).
export interface Bookmark {
  id: number;
  title: string;
  url: string;
}

export async function loadBookmarks(): Promise<Bookmark[]> {
  const response = await fetch("/api/bookmarks");
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }
  return (await response.json()) as Bookmark[];
}
