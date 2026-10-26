// Barrel module — re-exports the entire API surface from per-domain modules.
// Existing route imports (`import { fooBar } from '$lib/api/client'`) keep
// working unchanged. New code should import from the domain module directly.
// The analysis client (`$lib/api/analysis`) is not re-exported here: its
// names would clash with the automation client's (`getRun`, `PaginatedRuns`).

export * from "./applications";
export * from "./automation";
export * from "./credentials";
export * from "./generation";
export * from "./models";
export * from "./rankings";
export * from "./reports";
export * from "./runtime";
export * from "./statistics";
export * from "./system";
export * from "./tokens";
export * from "./users";
