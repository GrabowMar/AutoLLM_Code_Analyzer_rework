const STORAGE_KEY = 'llm-lab-preferences';

const VALID_COLORS = [
	'blue',
	'indigo',
	'purple',
	'pink',
	'red',
	'orange',
	'amber',
	'green',
	'teal',
	'cyan',
] as const;

const VALID_ITEMS_PER_PAGE = [10, 25, 50, 100] as const;

type AccentColor = (typeof VALID_COLORS)[number];
type ItemsPerPage = (typeof VALID_ITEMS_PER_PAGE)[number];

interface CookieConsent {
	essential: true;
	analytics: boolean;
	functional: boolean;
	ai: boolean;
}

interface UserPreferences {
	theme: 'light' | 'dark' | 'system';
	accentColor: AccentColor;
	sidebarCollapsed: boolean;
	itemsPerPage: ItemsPerPage;
	dateFormat: 'relative' | 'absolute' | 'iso';
	compactTables: boolean;
	showAdvancedOptions: boolean;
	autoRefresh: boolean;
	avatarColor: AccentColor;
	cookieConsent: CookieConsent;
}

const DEFAULT_PREFERENCES: UserPreferences = {
	theme: 'system',
	accentColor: 'blue',
	sidebarCollapsed: false,
	itemsPerPage: 25,
	dateFormat: 'relative',
	compactTables: false,
	showAdvancedOptions: false,
	autoRefresh: false,
	avatarColor: 'blue',
	cookieConsent: {
		essential: true,
		analytics: false,
		functional: false,
		ai: false,
	},
};

function loadFromStorage(): UserPreferences {
	if (typeof globalThis.localStorage === 'undefined') {
		return { ...DEFAULT_PREFERENCES, cookieConsent: { ...DEFAULT_PREFERENCES.cookieConsent } };
	}
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return { ...DEFAULT_PREFERENCES, cookieConsent: { ...DEFAULT_PREFERENCES.cookieConsent } };

		const parsed = JSON.parse(raw) as Partial<UserPreferences>;
		return validatePreferences(parsed);
	} catch {
		return { ...DEFAULT_PREFERENCES, cookieConsent: { ...DEFAULT_PREFERENCES.cookieConsent } };
	}
}

function isValidColor(value: unknown): value is AccentColor {
	return typeof value === 'string' && VALID_COLORS.includes(value as AccentColor);
}

function isValidItemsPerPage(value: unknown): value is ItemsPerPage {
	return typeof value === 'number' && VALID_ITEMS_PER_PAGE.includes(value as ItemsPerPage);
}

function validatePreferences(parsed: Partial<UserPreferences>): UserPreferences {
	const consent = parsed.cookieConsent;

	return {
		theme: ['light', 'dark', 'system'].includes(parsed.theme as string)
			? (parsed.theme as UserPreferences['theme'])
			: DEFAULT_PREFERENCES.theme,
		accentColor: isValidColor(parsed.accentColor)
			? parsed.accentColor
			: DEFAULT_PREFERENCES.accentColor,
		sidebarCollapsed:
			typeof parsed.sidebarCollapsed === 'boolean'
				? parsed.sidebarCollapsed
				: DEFAULT_PREFERENCES.sidebarCollapsed,
		itemsPerPage: isValidItemsPerPage(parsed.itemsPerPage)
			? parsed.itemsPerPage
			: DEFAULT_PREFERENCES.itemsPerPage,
		dateFormat: ['relative', 'absolute', 'iso'].includes(parsed.dateFormat as string)
			? (parsed.dateFormat as UserPreferences['dateFormat'])
			: DEFAULT_PREFERENCES.dateFormat,
		compactTables:
			typeof parsed.compactTables === 'boolean'
				? parsed.compactTables
				: DEFAULT_PREFERENCES.compactTables,
		showAdvancedOptions:
			typeof parsed.showAdvancedOptions === 'boolean'
				? parsed.showAdvancedOptions
				: DEFAULT_PREFERENCES.showAdvancedOptions,
		autoRefresh:
			typeof parsed.autoRefresh === 'boolean'
				? parsed.autoRefresh
				: DEFAULT_PREFERENCES.autoRefresh,
		avatarColor: isValidColor(parsed.avatarColor)
			? parsed.avatarColor
			: DEFAULT_PREFERENCES.avatarColor,
		cookieConsent: {
			essential: true,
			analytics:
				typeof consent?.analytics === 'boolean'
					? consent.analytics
					: DEFAULT_PREFERENCES.cookieConsent.analytics,
			functional:
				typeof consent?.functional === 'boolean'
					? consent.functional
					: DEFAULT_PREFERENCES.cookieConsent.functional,
			ai:
				typeof consent?.ai === 'boolean'
					? consent.ai
					: DEFAULT_PREFERENCES.cookieConsent.ai,
		},
	};
}

function saveToStorage(prefs: UserPreferences): void {
	if (typeof globalThis.localStorage === 'undefined') return;
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
	} catch {
		// Storage full or unavailable — silently ignore
	}
}

function createPreferences() {
	const initial = loadFromStorage();

	let theme = $state<UserPreferences['theme']>(initial.theme);
	let accentColor = $state<AccentColor>(initial.accentColor);
	let sidebarCollapsed = $state(initial.sidebarCollapsed);
	let itemsPerPage = $state<ItemsPerPage>(initial.itemsPerPage);
	let dateFormat = $state<UserPreferences['dateFormat']>(initial.dateFormat);
	let compactTables = $state(initial.compactTables);
	let showAdvancedOptions = $state(initial.showAdvancedOptions);
	let autoRefresh = $state(initial.autoRefresh);
	let avatarColor = $state<AccentColor>(initial.avatarColor);
	let cookieConsent = $state<CookieConsent>({ ...initial.cookieConsent });

	$effect(() => {
		const snapshot: UserPreferences = {
			theme,
			accentColor,
			sidebarCollapsed,
			itemsPerPage,
			dateFormat,
			compactTables,
			showAdvancedOptions,
			autoRefresh,
			avatarColor,
			cookieConsent: { ...cookieConsent },
		};
		saveToStorage(snapshot);
	});

	function setTheme(value: UserPreferences['theme']) {
		theme = value;
	}

	function setAccentColor(value: AccentColor) {
		if (isValidColor(value)) accentColor = value;
	}

	function setSidebarCollapsed(value: boolean) {
		sidebarCollapsed = value;
	}

	function toggleSidebar() {
		sidebarCollapsed = !sidebarCollapsed;
	}

	function setItemsPerPage(value: ItemsPerPage) {
		if (isValidItemsPerPage(value)) itemsPerPage = value;
	}

	function setDateFormat(value: UserPreferences['dateFormat']) {
		dateFormat = value;
	}

	function setCompactTables(value: boolean) {
		compactTables = value;
	}

	function setShowAdvancedOptions(value: boolean) {
		showAdvancedOptions = value;
	}

	function setAutoRefresh(value: boolean) {
		autoRefresh = value;
	}

	function setAvatarColor(value: AccentColor) {
		if (isValidColor(value)) avatarColor = value;
	}

	function setCookieConsent(value: Partial<Omit<CookieConsent, 'essential'>>) {
		cookieConsent = {
			essential: true,
			analytics: value.analytics ?? cookieConsent.analytics,
			functional: value.functional ?? cookieConsent.functional,
			ai: value.ai ?? cookieConsent.ai,
		};
	}

	function resetPreferences() {
		theme = DEFAULT_PREFERENCES.theme;
		accentColor = DEFAULT_PREFERENCES.accentColor;
		sidebarCollapsed = DEFAULT_PREFERENCES.sidebarCollapsed;
		itemsPerPage = DEFAULT_PREFERENCES.itemsPerPage;
		dateFormat = DEFAULT_PREFERENCES.dateFormat;
		compactTables = DEFAULT_PREFERENCES.compactTables;
		showAdvancedOptions = DEFAULT_PREFERENCES.showAdvancedOptions;
		autoRefresh = DEFAULT_PREFERENCES.autoRefresh;
		avatarColor = DEFAULT_PREFERENCES.avatarColor;
		cookieConsent = { ...DEFAULT_PREFERENCES.cookieConsent };
	}

	function exportPreferences(): string {
		const snapshot: UserPreferences = {
			theme,
			accentColor,
			sidebarCollapsed,
			itemsPerPage,
			dateFormat,
			compactTables,
			showAdvancedOptions,
			autoRefresh,
			avatarColor,
			cookieConsent: { ...cookieConsent },
		};
		return JSON.stringify(snapshot, null, 2);
	}

	function importPreferences(json: string): boolean {
		try {
			const parsed = JSON.parse(json) as Partial<UserPreferences>;
			const validated = validatePreferences(parsed);

			theme = validated.theme;
			accentColor = validated.accentColor;
			sidebarCollapsed = validated.sidebarCollapsed;
			itemsPerPage = validated.itemsPerPage;
			dateFormat = validated.dateFormat;
			compactTables = validated.compactTables;
			showAdvancedOptions = validated.showAdvancedOptions;
			autoRefresh = validated.autoRefresh;
			avatarColor = validated.avatarColor;
			cookieConsent = { ...validated.cookieConsent };

			return true;
		} catch {
			return false;
		}
	}

	return {
		get theme() {
			return theme;
		},
		get accentColor() {
			return accentColor;
		},
		get sidebarCollapsed() {
			return sidebarCollapsed;
		},
		get itemsPerPage() {
			return itemsPerPage;
		},
		get dateFormat() {
			return dateFormat;
		},
		get compactTables() {
			return compactTables;
		},
		get showAdvancedOptions() {
			return showAdvancedOptions;
		},
		get autoRefresh() {
			return autoRefresh;
		},
		get avatarColor() {
			return avatarColor;
		},
		get cookieConsent() {
			return cookieConsent;
		},
		setTheme,
		setAccentColor,
		setSidebarCollapsed,
		toggleSidebar,
		setItemsPerPage,
		setDateFormat,
		setCompactTables,
		setShowAdvancedOptions,
		setAutoRefresh,
		setAvatarColor,
		setCookieConsent,
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

export type { UserPreferences, AccentColor, CookieConsent, ItemsPerPage };
export { VALID_COLORS, VALID_ITEMS_PER_PAGE, DEFAULT_PREFERENCES };
