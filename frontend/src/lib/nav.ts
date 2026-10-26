// Single source of truth for app navigation: sidebar, mobile menu and breadcrumbs.
import type { Component } from "svelte";
import LayoutDashboard from "@lucide/svelte/icons/layout-dashboard";
import Boxes from "@lucide/svelte/icons/boxes";
import Library from "@lucide/svelte/icons/library";
import WandSparkles from "@lucide/svelte/icons/wand-sparkles";
import Zap from "@lucide/svelte/icons/zap";
import GitCompareArrows from "@lucide/svelte/icons/git-compare-arrows";
import AppWindow from "@lucide/svelte/icons/app-window";
import ScanSearch from "@lucide/svelte/icons/scan-search";
import Microscope from "@lucide/svelte/icons/microscope";
import FlaskConical from "@lucide/svelte/icons/flask-conical";
import SlidersHorizontal from "@lucide/svelte/icons/sliders-horizontal";
import Container from "@lucide/svelte/icons/container";
import Trophy from "@lucide/svelte/icons/trophy";
import ChartColumn from "@lucide/svelte/icons/chart-column";
import FileText from "@lucide/svelte/icons/file-text";
import BookOpen from "@lucide/svelte/icons/book-open";
import Activity from "@lucide/svelte/icons/activity";

export interface NavItem {
  label: string;
  href: string;
  icon: Component;
}

export interface NavSection {
  title: string;
  items: NavItem[];
  staffOnly?: boolean;
}

// Grouped by the research workflow: build → generate → evaluate → compare.
export const navSections: NavSection[] = [
  {
    title: "Overview",
    items: [{ label: "Dashboard", href: "/", icon: LayoutDashboard }],
  },
  {
    title: "Generate",
    items: [
      { label: "Models", href: "/models", icon: Boxes },
      { label: "Library", href: "/templates", icon: Library },
      {
        label: "Sample Generator",
        href: "/sample-generator",
        icon: WandSparkles,
      },
      { label: "Automation", href: "/automation", icon: Zap },
      { label: "Experiments", href: "/experiments", icon: GitCompareArrows },
    ],
  },
  {
    title: "Evaluate",
    items: [
      { label: "Applications", href: "/applications", icon: AppWindow },
      { label: "Studies", href: "/analysis/studies", icon: FlaskConical },
      { label: "Runs", href: "/analysis", icon: Microscope },
      {
        label: "Profiles",
        href: "/analysis/profiles",
        icon: SlidersHorizontal,
      },
      { label: "Tools", href: "/analysis/tools", icon: ScanSearch },
      { label: "Runtime", href: "/runtime", icon: Container },
    ],
  },
  {
    title: "Insights",
    items: [
      { label: "Rankings", href: "/rankings", icon: Trophy },
      { label: "Statistics", href: "/statistics", icon: ChartColumn },
      { label: "Reports", href: "/reports", icon: FileText },
    ],
  },
  {
    title: "Help",
    items: [{ label: "Docs", href: "/docs", icon: BookOpen }],
  },
  {
    title: "Admin",
    staffOnly: true,
    items: [{ label: "System", href: "/system", icon: Activity }],
  },
];

// Breadcrumb labels for routes that are not nav items.
const extraLabels: Record<string, string> = {
  "/about": "About",
  "/privacy": "Privacy Policy",
  "/users": "Users",
  "/users/settings": "Settings",
  "/models/compare": "Compare",
  "/models/import": "Import",
  // Runs, studies, profiles and tools share the /analysis prefix: the
  // breadcrumb names the area, the nav item names the runs list.
  "/analysis": "Analysis",
  "/analysis/create": "Ad-hoc run",
  "/analysis/studies/new": "New study",
  "/analysis/profiles/new": "New profile",
  "/analyzers": "Tools",
  "/automation/create": "New pipeline",
  "/automation/runs": "Runs",
  "/experiments/create": "New experiment",
  "/sample-generator/batches": "Batches",
  "/reports/create": "New report",
};

export const routeLabels: Record<string, string> = {
  ...Object.fromEntries(
    navSections.flatMap((s) => s.items.map((i) => [i.href, i.label])),
  ),
  ...extraLabels,
};

const navHrefs = navSections.flatMap((s) => s.items.map((i) => i.href));

function matchesHref(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

/**
 * True when `href` is the nav item for `pathname`. A more specific item
 * owns its subtree: `/analysis/profiles/x` activates Profiles, not Runs.
 */
export function isNavActive(pathname: string, href: string): boolean {
  if (!matchesHref(pathname, href)) return false;
  return !navHrefs.some(
    (other) =>
      other.length > href.length &&
      other.startsWith(href) &&
      matchesHref(pathname, other),
  );
}
