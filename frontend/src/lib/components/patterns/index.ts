// Pattern-analysis views (spec 7.9, 10.3 §4, WP-16). `PatternsTab` ({ studyId }) is mounted by WP-15's
// study record in its Patterns section (slot studies only).
export { default as PatternsTab } from "./PatternsTab.svelte";
export { default as PrevalenceTable } from "./PrevalenceTable.svelte";
export { default as ComparisonTable } from "./ComparisonTable.svelte";
export { default as ClusterPanel } from "./ClusterPanel.svelte";
export { default as FeatureMatrix } from "./FeatureMatrix.svelte";
export { default as DetectorFromSelection } from "./DetectorFromSelection.svelte";
export * from "./format";
