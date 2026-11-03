/**
 * Study building blocks (WP-15): builder, record, monitor, results, budget. Import single components by
 * path (`$lib/components/studies/builder/FactorTable.svelte`) or from here.
 */
export { default as StudyBuilder } from "./builder/StudyBuilder.svelte";
export { default as FactorTable } from "./builder/FactorTable.svelte";
export { default as LevelPicker } from "./builder/LevelPicker.svelte";
export { default as TaskPicker } from "./builder/TaskPicker.svelte";
export { default as SlotTargetPicker } from "./builder/SlotTargetPicker.svelte";
export { default as PowerHelper } from "./builder/PowerHelper.svelte";
export { default as ResourcePolicyPicker } from "./builder/ResourcePolicyPicker.svelte";
export { default as EvaluationSection } from "./builder/EvaluationSection.svelte";
export { default as AnalysisPlanSection } from "./builder/AnalysisPlanSection.svelte";
export { default as PromptOverrideDialog } from "./builder/PromptOverrideDialog.svelte";
export { default as PlanRail } from "./builder/PlanRail.svelte";
export { default as CostByLevelBar } from "./builder/CostByLevelBar.svelte";
export { default as ResolvedCellDrawer } from "./builder/ResolvedCellDrawer.svelte";
export { default as CellDetailView } from "./builder/CellDetailView.svelte";
export { default as LaunchDialog } from "./builder/LaunchDialog.svelte";
export { default as ComposerSection } from "./builder/ComposerSection.svelte";
export { default as ComposerSectionNav } from "./builder/ComposerSectionNav.svelte";
export { default as RunFormula } from "./builder/RunFormula.svelte";
export { default as StartBar } from "./builder/StartBar.svelte";
export { default as SummaryIssues } from "./builder/SummaryIssues.svelte";
export { default as StudyMasthead } from "./study/StudyMasthead.svelte";
export { default as StudySection } from "./study/StudySection.svelte";
export { default as StudyFigure } from "./study/StudyFigure.svelte";
export { default as LiveSpendStrip } from "./monitor/LiveSpendStrip.svelte";
export { default as PilotGateCard } from "./monitor/PilotGateCard.svelte";
export { default as RunStreamPanel } from "./monitor/RunStreamPanel.svelte";
export { default as ConditionGrid } from "./monitor/ConditionGrid.svelte";
export { default as CapacityStrip } from "./monitor/CapacityStrip.svelte";
export { default as ByFactorPanel } from "./results/ByFactorPanel.svelte";
export { default as AttritionTable } from "./results/AttritionTable.svelte";
export { default as LedgerTable } from "./budget/LedgerTable.svelte";
export { default as SpendCharts } from "./budget/SpendCharts.svelte";
export { default as ScheduleEditor } from "./automation/ScheduleEditor.svelte";
export { default as PostRunEditor } from "./automation/PostRunEditor.svelte";

export {
  BuilderState,
  PLAN_DEBOUNCE_MS,
  AUTOSAVE_MS,
} from "./builder/builder.svelte";
export { StudyLive } from "./monitor/live.svelte";
export * from "./builder/design";
export {
  expandGrid,
  countCells,
  unitCount,
  ExpansionError,
} from "./builder/expansion";
export * from "./builder/power";
export {
  fromIssues,
  groupIssues,
  issueTitle,
  parseFix,
  type SummaryIssue,
  type IssueAction,
  type FixAction,
} from "./builder/issues";
export {
  segmentText,
  buildOverrideRequest,
  EDITABLE_OWNERS,
  OWNER_LABELS,
} from "./builder/spans";
export * from "./format";
export { refreshSpendChip } from "./spend";
export {
  resolveStudyTab,
  tabsFor,
  STUDY_TABS,
  type StudyTab,
} from "./study/tabs";
