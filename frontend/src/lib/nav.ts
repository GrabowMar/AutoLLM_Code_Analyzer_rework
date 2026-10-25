// Single source of truth for app navigation: sidebar, mobile menu and breadcrumbs.
import type { Component } from "svelte";
import LayoutDashboard from "@lucide/svelte/icons/layout-dashboard";
import Boxes from "@lucide/svelte/icons/boxes";
import Library from "@lucide/svelte/icons/library";
import WandSparkles from "@lucide/svelte/icons/wand-sparkles";
import Zap from "@lucide/svelte/icons/zap";
import AppWindow from "@lucide/svelte/icons/app-window";
import ScanSearch from "@lucide/svelte/icons/scan-search";
import Microscope from "@lucide/svelte/icons/microscope";
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
    ],
  },
  {
    title: "Evaluate",
    items: [
      { label: "Applications", href: "/applications", icon: AppWindow },
      { label: "Analysis", href: "/analysis", icon: Microscope },
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
  "/privacy": "Privacy Policy",
  "/users": "Users",
  "/users/settings": "Settings",
  "/models/compare": "Compare",
  "/models/import": "Import",
  "/analysis/create": "New analysis",
  "/automation/create": "New automation",
  "/automation/runs": "Runs",
  "/experiments": "Automation",
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

export function isNavActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (
    href === "/automation" &&
    (pathname === "/experiments" || pathname.startsWith("/experiments/"))
  ) {
    return true;
  }
  return pathname === href || pathname.startsWith(href + "/");
}
