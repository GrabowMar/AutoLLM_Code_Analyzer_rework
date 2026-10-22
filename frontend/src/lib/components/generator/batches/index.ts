// Batch views (list, batch page) and the job page's batch links.
export { default as BatchProgress } from "./BatchProgress.svelte";
export { default as ResultsMatrix } from "./ResultsMatrix.svelte";
export { default as BatchJobsTable } from "./BatchJobsTable.svelte";

export * from "./batch-utils";
export {
  buildResultsMatrix,
  cellKey,
  computeStats,
  representativeJob,
  type CellStats,
  type MatrixCell,
  type MatrixColumn,
  type MatrixRow,
  type MatrixTrial,
  type ResultsMatrix as ResultsMatrixData,
} from "./matrix";

export function batchHref(id: string): string {
  return `/sample-generator/batches/${id}`;
}
