// Experiments UI: list rows, the single-page composer, the live monitor,
// results statistics and automation (spec §G.3–G.5).
export { default as ExperimentComposer } from "./ExperimentComposer.svelte";
export { default as PlanRail } from "./PlanRail.svelte";
export { default as EndpointPlanRow } from "./EndpointPlanRow.svelte";
export { default as SelectionSummary } from "./SelectionSummary.svelte";
export { default as ExecutionSettings } from "./ExecutionSettings.svelte";
export { default as AutomationSettings } from "./AutomationSettings.svelte";
export { default as ScheduleEditor } from "./ScheduleEditor.svelte";
export { default as PostRunEditor } from "./PostRunEditor.svelte";
export { default as CapacityStrip } from "./CapacityStrip.svelte";
export { default as ExperimentRow } from "./ExperimentRow.svelte";
export { default as ComparabilityTable } from "./ComparabilityTable.svelte";
export { default as ExperimentDesignSummary } from "./ExperimentDesignSummary.svelte";
export { default as Leaderboard } from "./Leaderboard.svelte";
export { default as PairedDeltas } from "./PairedDeltas.svelte";
export { default as SchedulesCard } from "./SchedulesCard.svelte";
export { default as PostRunCard } from "./PostRunCard.svelte";
export { default as EndpointLane } from "./monitor/EndpointLane.svelte";
export { default as ConditionGrid } from "./monitor/ConditionGrid.svelte";
export { default as ActivityFeed } from "./monitor/ActivityFeed.svelte";

export {
  ExperimentLive,
  type ActivityItem,
  type LiveConnection,
} from "./live.svelte";
export * from "./cells";
export * from "./composer";
export * from "./cron";
export * from "./format";
