"""Server-defined content for security blocks and observation reports.

``DEFAULT_SECURITY_CHECKLIST`` is the guided checklist a new security block
starts from (aligned with the OWASP Top 10 2021 for small CRUD web apps); each
item says concretely how to test it. ``OBSERVATION_CATEGORIES`` lists the
category values a participant can pick for bug and vulnerability reports.

The admin login that the platform's test deployment provides (``admin`` /
``admin``, the conformance contract default) is an artifact of the hosting, not
of the generated code; the checklist help and the codebook state it is out of
scope.
"""

from __future__ import annotations

import copy

PLATFORM_CREDENTIALS_NOTE = (
    "Out of scope: the admin login the study platform gives you (admin / admin) comes from the test "
    "deployment, not from the generated app. Do not report it as a weak or default password."
)

OWASP_2021: tuple[tuple[str, str], ...] = (
    ("A01", "A01:2021 Broken Access Control"),
    ("A02", "A02:2021 Cryptographic Failures"),
    ("A03", "A03:2021 Injection"),
    ("A04", "A04:2021 Insecure Design"),
    ("A05", "A05:2021 Security Misconfiguration"),
    ("A06", "A06:2021 Vulnerable and Outdated Components"),
    ("A07", "A07:2021 Identification and Authentication Failures"),
    ("A08", "A08:2021 Software and Data Integrity Failures"),
    ("A09", "A09:2021 Security Logging and Monitoring Failures"),
    ("A10", "A10:2021 Server-Side Request Forgery (SSRF)"),
)
_OWASP = dict(OWASP_2021)

DEFAULT_SECURITY_CHECKLIST: list[dict[str, str]] = [
    {
        "id": "access_control",
        "label": "Broken access control / IDOR",
        "help": (
            "Register two ordinary accounts (A and B). As A, create a record and note its id in the URL or API "
            "response. As B, try to read, change and delete A's record by swapping the id (for example "
            "GET/PUT/DELETE /api/items/<id> in the HTTP console). Also open admin-only pages and admin API "
            "endpoints as an ordinary user and while logged out. Vulnerable if B can see or change A's data, "
            "or a non-admin reaches admin functions."
        ),
        "category": _OWASP["A01"],
    },
    {
        "id": "authentication_session",
        "label": "Authentication and session handling",
        "help": (
            "Try to register with a trivial password (for example '123'); compare the login errors for an "
            "existing and a non-existing account (do they reveal which emails exist?); log out, then replay a "
            "request with the old token or cookie in the HTTP console; check that tokens never appear in URLs "
            "and that API responses never echo passwords. Vulnerable if an old session keeps working after "
            "logout, trivially weak passwords are accepted, or accounts can be enumerated. "
            + PLATFORM_CREDENTIALS_NOTE
        ),
        "category": _OWASP["A07"],
    },
    {
        "id": "injection",
        "label": "Injection (SQL, command)",
        "help": (
            "In search boxes, filters, login fields and id parameters enter values such as ', ' OR '1'='1, "
            '1 OR 1=1 and "; -- and watch for database errors, extra records or a login that succeeds '
            "without the right password. Where a feature processes files or names, also try ; ls and "
            "$(id). Vulnerable if the input changes the query or command instead of being treated as data."
        ),
        "category": _OWASP["A03"],
    },
    {
        "id": "xss",
        "label": "Cross-site scripting (XSS)",
        "help": (
            "Enter <img src=x onerror=alert(1)> and <script>alert(1)</script> in every text field whose value "
            "is shown later (names, titles, comments, profile fields), then view the page as yourself and as "
            "another user; also try the payload in URL query parameters that are echoed on the page. "
            "Vulnerable if a dialog appears or the markup is rendered instead of shown as plain text."
        ),
        "category": _OWASP["A03"],
    },
    {
        "id": "csrf",
        "label": "Cross-site request forgery (CSRF)",
        "help": (
            "Find out how the app authenticates requests (browser developer tools, Network tab). If it uses "
            "cookies, check whether state-changing requests (POST/PUT/PATCH/DELETE) also require an anti-CSRF "
            "token or a custom header, and whether any state change is possible with a plain GET link. Apps "
            "that authenticate only with an 'Authorization: Bearer' header are not CSRF-prone: mark this item "
            "not vulnerable and say why in the note."
        ),
        "category": _OWASP["A01"],
    },
    {
        "id": "data_exposure",
        "label": "Sensitive data exposure and error leakage",
        "help": (
            "Inspect API responses (HTTP console or the Network tab) for password hashes, tokens, other "
            "users' emails or private fields that the page does not need. Then provoke errors: malformed "
            "JSON, wrong types, very long values, ids that do not exist. Vulnerable if responses leak secrets "
            "or other users' data, or errors show stack traces, SQL, file paths or a debug page."
        ),
        "category": _OWASP["A02"],
    },
    {
        "id": "misconfiguration",
        "label": "Security misconfiguration and CORS",
        "help": (
            "Send a request with the header 'Origin: https://evil.example' and look at "
            "Access-Control-Allow-Origin in the response (reflecting any origin, especially together with "
            "Access-Control-Allow-Credentials: true, is vulnerable). Look for debug mode, directory listings, "
            "exposed configuration or backup files, and default or sample accounts that the generated code "
            "itself creates. " + PLATFORM_CREDENTIALS_NOTE
        ),
        "category": _OWASP["A05"],
    },
    {
        "id": "input_validation",
        "label": "Input validation and business logic",
        "help": (
            "Submit values the business rules should forbid: negative quantities or prices, huge numbers, "
            "empty or very long strings, wrong types, dates in the past, duplicate entries. Skip steps of "
            "multi-step flows by calling the final endpoint directly, and repeat one-time actions (double "
            "submit). Vulnerable if the server accepts what the rules forbid (validation only in the browser)."
        ),
        "category": _OWASP["A04"],
    },
    {
        "id": "mass_assignment",
        "label": "Mass assignment and privilege escalation",
        "help": (
            "When registering or updating your profile or a record, add fields the form does not offer, using "
            'the HTTP console: "is_admin": true, "role": "admin", "user_id" or "owner_id" set to another '
            "user's id, a different price or status. Then reload and check whether your account gained "
            "privileges or the record changed owner or state. Vulnerable if any such field is accepted."
        ),
        "category": _OWASP["A01"],
    },
    {
        "id": "rate_limiting",
        "label": "Rate limiting and brute force",
        "help": (
            "Send about 20-30 wrong-password logins for one account in quick succession through the HTTP "
            "console and see whether the app slows down, locks the account or answers 429. Do the same for "
            "other expensive or abusable endpoints (registration, password reset). Keep it to a few dozen "
            "requests: do not try to take the app down. Vulnerable if unlimited attempts are accepted."
        ),
        "category": _OWASP["A07"],
    },
]

BUG_CATEGORIES: list[dict[str, str]] = [
    {"value": "functional", "label": "Functional (wrong or missing behaviour)"},
    {"value": "ui", "label": "User interface (layout, display, text)"},
    {"value": "crash", "label": "Crash or error page"},
    {"value": "performance", "label": "Performance (slow, hangs)"},
    {"value": "data", "label": "Data (lost, wrong or inconsistent data)"},
    {"value": "other", "label": "Other"},
]

VULNERABILITY_CATEGORIES: list[dict[str, str]] = [
    *({"value": code, "label": label} for code, label in OWASP_2021),
    {"value": "other", "label": "Other"},
]

#: Category choices per observation kind (``note`` takes none).
OBSERVATION_CATEGORIES: dict[str, list[dict[str, str]]] = {
    "bug": BUG_CATEGORIES,
    "vulnerability": VULNERABILITY_CATEGORIES,
    "note": [],
}

#: Allowed ``Observation.category`` values per kind ("" is always allowed).
OBSERVATION_CATEGORY_VALUES: dict[str, frozenset[str]] = {
    kind: frozenset(option["value"] for option in options) for kind, options in OBSERVATION_CATEGORIES.items()
}


def default_security_checklist() -> list[dict[str, str]]:
    """A fresh copy of :data:`DEFAULT_SECURITY_CHECKLIST` (callers may mutate it)."""
    return copy.deepcopy(DEFAULT_SECURITY_CHECKLIST)
