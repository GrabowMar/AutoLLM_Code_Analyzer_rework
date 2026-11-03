// Single source of truth for app navigation: sidebar, mobile menu and breadcrumbs.
import type { Component } from "svelte";
import LayoutDashboard from "@lucide/svelte/icons/layout-dashboard";
import Boxes from "@lucide/svelte/icons/boxes";
import Library from "@lucide/svelte/icons/library";
import Scissors from "@lucide/svelte/icons/scissors";
import Wallet from "@lucide/svelte/icons/wallet";
import FlaskConical from "@lucide/svelte/icons/flask-conical";
import Server from "@lucide/svelte/icons/server";
import AppWindow from "@lucide/svelte/icons/app-window";
import ScanSearch from "@lucide/svelte/icons/scan-search";
import Microscope from "@lucide/svelte/icons/microscope";
import Container from "@lucide/svelte/icons/container";
import Trophy from "@lucide/svelte/icons/trophy";
import ChartColumn from "@lucide/svelte/icons/chart-column";
import FileText from "@lucide/svelte/icons/file-text";
import BookOpen from "@lucide/svelte/icons/book-open";
import Activity from "@lucide/svelte/icons/activity";
import ClipboardCheck from "@lucide/svelte/icons/clipboard-check";

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
      { label: "Endpoints", href: "/endpoints", icon: Server },
      { label: "Library", href: "/library", icon: Library },
      { label: "Studies", href: "/studies", icon: FlaskConical },
      { label: "Slots", href: "/slots/new", icon: Scissors },
      { label: "Budget", href: "/budget", icon: Wallet },
    ],
  },
  {
    title: "Evaluate",
    items: [
      { label: "Applications", href: "/applications", icon: AppWindow },
      { label: "Analysis", href: "/analysis", icon: Microscope },
      { label: "Human eval", href: "/human-eval", icon: ClipboardCheck },
      { label: "Analyzers", href: "/analyzers", icon: ScanSearch },
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
  "/privacy": "Privacy & data",
  "/users/settings": "Settings",
  "/models/compare": "Compare",
  "/models/import": "Import",
  "/analysis/create": "New analysis",
  "/studies/new": "New study",
  "/endpoints/new": "New endpoint",
  "/slots": "Slots",
  "/samples": "Samples",
  "/library/prompts/playground": "Prompt playground",
  "/library/tasks": "Tasks",
  "/library/stacks": "Stacks",
  "/library/starters": "Starters",
  "/library/styles": "Styles",
  "/library/guidance": "Guidance",
  "/library/prompts": "Prompt templates",
  "/library/prompt-sets": "Prompt sets",
  "/library/harness-presets": "Harness presets",
  "/library/sampling-presets": "Sampling presets",
  "/library/resource-policies": "Budget presets",
  "/library/eval-suites": "Eval suites",
  "/library/detectors": "Detectors",
  "/reports/create": "New report",
  "/human-eval/new": "New task",
};

export const routeLabels: Record<string, string> = {
  ...Object.fromEntries(
    navSections.flatMap((s) => s.items.map((i) => [i.href, i.label])),
  ),
  ...extraLabels,
};

export function isNavActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

/** The nav section (and its 1-based position) that owns a path, for page eyebrows. */
export function navSectionFor(
  pathname: string,
): { index: number; section: NavSection } | null {
  let found: { index: number; section: NavSection } | null = null;
  let bestLen = -1;
  for (const [i, section] of navSections.entries()) {
    for (const item of section.items) {
      if (isNavActive(pathname, item.href) && item.href.length > bestLen) {
        found = { index: i + 1, section };
        bestLen = item.href.length;
      }
    }
  }
  return found;
}
