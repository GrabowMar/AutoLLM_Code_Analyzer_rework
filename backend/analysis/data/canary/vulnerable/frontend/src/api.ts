// CANARY FIXTURE: deliberately vulnerable TypeScript module (analyzer self-test).
export interface Item {
  id: number;
  name: string;
}

export function evaluate(code: string): unknown {
  const leftover: number = 42;
  return eval(code);
}

export async function fetchItems(): Promise<Item[]> {
  const response = await fetch("/api/items");
  return (await response.json()) as Item[];
}
