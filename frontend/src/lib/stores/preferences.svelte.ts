import { setMode, userPrefersMode } from "mode-watcher";

const STORAGE_KEY = "llm-lab-preferences";

const VALID_COLORS = [
  "blue",
  "indigo",
  "purple",
  "pink",
  "red",
  "orange",
  "amber",
  "green",
  "teal",
  "cyan",
] as const;

const VALID_THEMES = ["light", "dark", "system"] as const;

type AvatarColor = (typeof VALID_COLORS)[number];
type ThemeMode = (typeof VALID_THEMES)[number];

/** Locally stored UI preferences. The theme itself lives in mode-watcher's own storage key. */
interface StoredPreferences {
  sidebarCollapsed: boolean;
  compactTables: boolean;
  avatarColor: AvatarColor;
}

/** What export/import carries: the stored preferences plus the theme. */
interface UserPreferences extends StoredPreferences {
  theme: ThemeMode;
}

const DEFAULT_THEME: ThemeMode = "dark";

const DEFAULT_PREFERENCES: StoredPreferences = {
  sidebarCollapsed: false,
  compactTables: false,
  avatarColor: "blue",
};

function isValidColor(value: unknown): value is AvatarColor {
  return (
    typeof value === "string" && VALID_COLORS.includes(value as AvatarColor)
  );
}

function isValidTheme(value: unknown): value is ThemeMode {
  return typeof value === "string" && VALID_THEMES.includes(value as ThemeMode);
}

function validatePreferences(
  parsed: Partial<StoredPreferences>,
): StoredPreferences {
  return {
    sidebarCollapsed:
      typeof parsed.sidebarCollapsed === "boolean"
        ? parsed.sidebarCollapsed
        : DEFAULT_PREFERENCES.sidebarCollapsed,
    compactTables:
      typeof parsed.compactTables === "boolean"
        ? parsed.compactTables
        : DEFAULT_PREFERENCES.compactTables,
    avatarColor: isValidColor(parsed.avatarColor)
      ? parsed.avatarColor
      : DEFAULT_PREFERENCES.avatarColor,
  };
}

function loadFromStorage(): StoredPreferences {
  if (typeof globalThis.localStorage === "undefined") {
    return { ...DEFAULT_PREFERENCES };
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_PREFERENCES };
    return validatePreferences(JSON.parse(raw) as Partial<StoredPreferences>);
  } catch {
    return { ...DEFAULT_PREFERENCES };
  }
}

function saveToStorage(prefs: StoredPreferences): void {
  if (typeof globalThis.localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Storage full or unavailable — silently ignore
  }
}

function createPreferences() {
  const initial = loadFromStorage();

  let sidebarCollapsed = $state(initial.sidebarCollapsed);
  let compactTables = $state(initial.compactTables);
  let avatarColor = $state<AvatarColor>(initial.avatarColor);

  $effect(() => {
    saveToStorage({ sidebarCollapsed, compactTables, avatarColor });
  });

  function setTheme(value: ThemeMode) {
    if (isValidTheme(value)) setMode(value);
  }

  function setSidebarCollapsed(value: boolean) {
    sidebarCollapsed = value;
  }

  function toggleSidebar() {
    sidebarCollapsed = !sidebarCollapsed;
  }

  function setCompactTables(value: boolean) {
    compactTables = value;
  }

  function setAvatarColor(value: AvatarColor) {
    if (isValidColor(value)) avatarColor = value;
  }

  function resetPreferences() {
    setMode(DEFAULT_THEME);
    sidebarCollapsed = DEFAULT_PREFERENCES.sidebarCollapsed;
    compactTables = DEFAULT_PREFERENCES.compactTables;
    avatarColor = DEFAULT_PREFERENCES.avatarColor;
  }

  function exportPreferences(): string {
    const snapshot: UserPreferences = {
      theme: userPrefersMode.current,
      sidebarCollapsed,
      compactTables,
      avatarColor,
    };
    return JSON.stringify(snapshot, null, 2);
  }

  /** Applies a JSON export; unknown or invalid fields fall back to defaults. False when not a JSON object. */
  function importPreferences(json: string): boolean {
    try {
      const parsed = JSON.parse(json) as Partial<UserPreferences>;
      if (
        parsed === null ||
        typeof parsed !== "object" ||
        Array.isArray(parsed)
      )
        return false;
      const validated = validatePreferences(parsed);
      setMode(isValidTheme(parsed.theme) ? parsed.theme : DEFAULT_THEME);
      sidebarCollapsed = validated.sidebarCollapsed;
      compactTables = validated.compactTables;
      avatarColor = validated.avatarColor;
      return true;
    } catch {
      return false;
    }
  }

  return {
    get theme(): ThemeMode {
      return userPrefersMode.current;
    },
    get sidebarCollapsed() {
      return sidebarCollapsed;
    },
    get compactTables() {
      return compactTables;
    },
    get avatarColor() {
      return avatarColor;
    },
    setTheme,
    setSidebarCollapsed,
    toggleSidebar,
    setCompactTables,
    setAvatarColor,
    resetPreferences,
    exportPreferences,
    importPreferences,
  };
}

let preferencesInstance: ReturnType<typeof createPreferences> | null = null;

export function getPreferences() {
  if (!preferencesInstance) {
    preferencesInstance = createPreferences();
  }
  return preferencesInstance;
}

export type { UserPreferences, AvatarColor, ThemeMode };
export { VALID_COLORS, VALID_THEMES, DEFAULT_PREFERENCES, DEFAULT_THEME };
