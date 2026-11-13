/** Ask the header SpendChip to refetch now (after a launch, a top-up or a cap change). */
export function refreshSpendChip(): void {
  if (typeof window !== "undefined")
    window.dispatchEvent(new Event("llmlab:spend-changed"));
}
