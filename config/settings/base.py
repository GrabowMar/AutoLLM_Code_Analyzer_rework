# ruff: noqa: ERA001
"""Base settings to build other settings files upon."""

import ssl
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve(strict=True).parent.parent.parent
# backend/
APPS_DIR = BASE_DIR / "backend"
env = environ.Env()

READ_DOT_ENV_FILE = env.bool("DJANGO_READ_DOT_ENV_FILE", default=False)
if READ_DOT_ENV_FILE:
    # OS environment variables take precedence over variables from .env
    env.read_env(str(BASE_DIR / ".env"))

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = env.bool("DJANGO_DEBUG", False)
# Local time zone. Choices are
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# though not all of them may be available with every OS.
# In Windows, this must be set to your system time zone.
TIME_ZONE = "UTC"
# https://docs.djangoproject.com/en/dev/ref/settings/#language-code
LANGUAGE_CODE = "en-us"
# https://docs.djangoproject.com/en/dev/ref/settings/#languages
# from django.utils.translation import gettext_lazy as _
# LANGUAGES = [
#     ('en', _('English')),
#     ('fr-fr', _('French')),
#     ('pt-br', _('Portuguese')),
# ]
# https://docs.djangoproject.com/en/dev/ref/settings/#site-id
SITE_ID = 1
# https://docs.djangoproject.com/en/dev/ref/settings/#use-i18n
USE_I18N = True
# https://docs.djangoproject.com/en/dev/ref/settings/#use-tz
USE_TZ = True
# https://docs.djangoproject.com/en/dev/ref/settings/#locale-paths
LOCALE_PATHS = [str(BASE_DIR / "locale")]

# DATABASES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#databases
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
# https://docs.djangoproject.com/en/stable/ref/settings/#std:setting-DEFAULT_AUTO_FIELD

# URLS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#root-urlconf
ROOT_URLCONF = "config.urls"
# https://docs.djangoproject.com/en/dev/ref/settings/#wsgi-application
WSGI_APPLICATION = "config.wsgi.application"

# APPS
# ------------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # "django.contrib.humanize", # Handy template tags
    "django.contrib.admin",
    "django.forms",
]
THIRD_PARTY_APPS = [
    "allauth",
    "allauth.account",
    "allauth.headless",
    "allauth.mfa",
    "allauth.socialaccount",
    "django_celery_beat",
    "corsheaders",
]

LOCAL_APPS = [
    "backend.users",
    "backend.tokens",
    "backend.credentials",
    "backend.llm_models",
    "backend.generation",
    "backend.analysis",
    "backend.statistics",
    "backend.rankings",
    "backend.reports",
    "backend.runtime",
    "backend.realtime",
    "backend.export",
    "backend.docs",
    "backend.system",
    "backend.automation",
    "backend.inference",
    "backend.execution",
    "backend.integrations",
]
# https://docs.djangoproject.com/en/dev/ref/settings/#installed-apps
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# MIGRATIONS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#migration-modules
MIGRATION_MODULES = {"sites": "backend.contrib.sites.migrations"}

# AUTHENTICATION
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#authentication-backends
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
# https://docs.djangoproject.com/en/dev/ref/settings/#auth-user-model
AUTH_USER_MODEL = "users.User"
# https://docs.djangoproject.com/en/dev/ref/settings/#login-redirect-url
LOGIN_REDIRECT_URL = "users:redirect"
# https://docs.djangoproject.com/en/dev/ref/settings/#login-url
# allauth is in HEADLESS_ONLY mode (no classic `account_login` URL is registered);
# login lives on the SvelteKit frontend at /auth/login/.
LOGIN_URL = "/auth/login/"

# PASSWORDS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
PASSWORD_HASHERS = [
    # https://docs.djangoproject.com/en/dev/topics/auth/passwords/#using-argon2-with-django
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]
# https://docs.djangoproject.com/en/dev/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# MIDDLEWARE
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#middleware
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Route <name>.<APPS_DOMAIN> straight to the app proxy before any
    # session/CSRF/auth processing (apps are a separate, public origin).
    "backend.runtime.middleware.AppSubdomainProxyMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "backend.users.middleware.RememberMeMiddleware",
    "backend.users.middleware.ForceCsrfCookieMiddleware",
]

# STATIC
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#static-root
STATIC_ROOT = str(BASE_DIR / "staticfiles")
# https://docs.djangoproject.com/en/dev/ref/settings/#static-url
STATIC_URL = "/static/"
# https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#std:setting-STATICFILES_DIRS
STATICFILES_DIRS = [str(APPS_DIR / "static")]
# https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#staticfiles-finders
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

# MEDIA
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#media-root
MEDIA_ROOT = str(APPS_DIR / "media")
# https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = "/media/"

# Agent-mode generation (OpenCode harness in an apps-daemon sandbox)
# ------------------------------------------------------------------------------
# Full run records (LLM call log, harness events, patch) land under
# GENERATION_RUNS_ROOT/<job_id>/; job rows keep summaries plus sha256s.
GENERATION_RUNS_ROOT = str(Path(MEDIA_ROOT) / "generation_runs")
# Stack a generation request runs on when neither the request nor its profile
# names one.
GENERATION_DEFAULT_STACK = env.str("GENERATION_DEFAULT_STACK", default="flask-react")
# Harness releases baked into the sandbox images (images/agent-*). Changing one
# changes the image tag and every new job's run_fingerprint.
AGENT_DEFAULT_HARNESS = env.str("AGENT_DEFAULT_HARNESS", default="opencode")
AGENT_OPENCODE_VERSION = env.str("AGENT_OPENCODE_VERSION", default="1.18.35")
AGENT_MINI_SWE_AGENT_VERSION = env.str("AGENT_MINI_SWE_AGENT_VERSION", default="2.4.6")
AGENT_AIDER_VERSION = env.str("AGENT_AIDER_VERSION", default="0.86.2")
# Defaults for every agent run; experiments / jobs may override them
# (generation/services/agent/config.py validates the ranges). The request
# budget binds; wall clock and USD are safety nets (see config.py).
AGENT_MAX_LLM_REQUESTS = env.int("AGENT_MAX_LLM_REQUESTS", default=100)
AGENT_WALL_CLOCK_SECONDS = env.int("AGENT_WALL_CLOCK_SECONDS", default=3600)
AGENT_MAX_TOTAL_TOKENS = env.int("AGENT_MAX_TOTAL_TOKENS", default=30_000_000)
AGENT_MAX_COST_USD = env.float("AGENT_MAX_COST_USD", default=5.0)
AGENT_SAMPLING_POLICY = env.str("AGENT_SAMPLING_POLICY", default="provider_default")
# Build, run and grade every agent sample with the hidden conformance suite
# right after its run (generation/services/evaluation/).
AGENT_AUTO_EVALUATE = env.bool("AGENT_AUTO_EVALUATE", default=True)
AGENT_NETWORK_POLICY = env.str("AGENT_NETWORK_POLICY", default="packages")
# Initial ExecutorSettings.sandbox_slots (sandboxes running at once; editable at
# runtime by staff). Each may use AGENT_SANDBOX_MEM_LIMIT of the apps daemon's
# own cap (APPS_DIND_MEM_LIMIT, shared with the sample apps), so raise both
# together. Only sandbox_mem_limit / nano_cpus are frozen into job snapshots
# (runs are only comparable under the same caps); the concurrency is not.
AGENT_MAX_CONCURRENT_RUNS = env.int("AGENT_MAX_CONCURRENT_RUNS", default=1)
AGENT_SANDBOX_MEM_LIMIT = env.str("AGENT_SANDBOX_MEM_LIMIT", default="3g")
AGENT_SANDBOX_NANO_CPUS = env.int("AGENT_SANDBOX_NANO_CPUS", default=2_000_000_000)
# Provider routing used when a run sets no explicit ``provider`` llm param.
AGENT_DEFAULT_PROVIDER_ROUTING = {
    "require_parameters": True,
    "quantizations": ["fp8", "fp16", "bf16", "fp32", "unknown"],
    "allow_fallbacks": True,
}

# ── Execution (backend/execution) ──
EXECUTOR_TICK_SECONDS = env.float("EXECUTOR_TICK_SECONDS", default=2.0)
EXECUTOR_LEASE_SECONDS = env.int("EXECUTOR_LEASE_SECONDS", default=90)
EXECUTOR_HEARTBEAT_SECONDS = env.int("EXECUTOR_HEARTBEAT_SECONDS", default=20)
EXECUTOR_LEADER_LOCK_KEY = 0x6C6C6D6C6162  # "llmlab"
EXECUTOR_MAX_DB_THREADS = 48
EXECUTOR_LOST_RETRIES = 10  # executor_lost requeues; separate from infra_retries
EXECUTOR_STARVATION_S = 600  # residency starvation guard (spec §C.4 rule 4)
EVAL_MAX_REQUEUES = 2
# AUTHORITATIVE for the memory rule (docker info MemTotal inside apps-dind reports host RAM).
APPS_DIND_MEM_LIMIT = env.str("APPS_DIND_MEM_LIMIT", default="4g")
APPS_DIND_APP_RESERVE = env.str("APPS_DIND_APP_RESERVE", default="1g")
EVAL_SLOT_MEM = "1536m"  # app 1g + tester 256m + slack (evaluation/runner.py)
ANALYSIS_WORKSPACE_MEM = "1g"  # runtime/services/docker_manager.py
SCHEDULE_MIN_INTERVAL_S = 900
# ── Inference ──
LLM_PROXY_BIND = env.str("LLM_PROXY_BIND", default="172.30.99.2:8090")
LLM_PROXY_UPSTREAM = env.str("LLM_PROXY_UPSTREAM", default="http://172.30.99.2:8090/v1")
LLM_GATEWAY_EGRESS_NETWORK = "llmlab-gw-egress"
LLM_GATEWAY_EGRESS_BRIDGE = "llmlab-gwegr"  # com.docker.network.bridge.name (firewall -i match)
LLM_GATEWAY_EGRESS_SUBNET = env.str("LLM_GATEWAY_EGRESS_SUBNET", default="10.213.0.0/24")
LLM_PROXY_MAX_BODY_BYTES = 16 * 1024 * 1024
RELAY_POLL_TIMEOUT_S = 25
RELAY_ACK_LEASE_S = 60  # 2 x poll timeout; extended on ack
RELAY_SESSION_TTL_S = 120  # > relay retry gaps, so a short network blip keeps in-flight work
RELAY_MAX_BODY_BYTES = 8 * 1024 * 1024  # request bodies (gzip ok); decompressed cap 32 MB
RELAY_RATE_LIMIT_PER_S = 20
RELAY_MIN_VERSION = "1.0.0"
RELAY_INVENTORY_MAX_MODELS = 500
RELAY_MAX_ENDPOINTS_PER_USER = 100  # non-staff relay endpoints per owner
RELAY_MAX_TOKENS_PER_ENDPOINT = 20  # live (unrevoked, unexpired) tokens; staff exempt
# frontend + Caddy addresses; client IP = right-most untrusted X-Forwarded-For hop
TRUSTED_PROXY_CIDRS = env.list("TRUSTED_PROXY_CIDRS", default=[])
# "cidr:port", e.g. "100.64.0.0/10:11434"
INFERENCE_DIRECT_ALLOWED_TARGETS = env.list("INFERENCE_DIRECT_ALLOWED_TARGETS", default=[])
INFERENCE_BLOCKED_HOSTS = [
    "postgres",
    "redis",
    "django",
    "apps-dind",
    "mailpit",
    "flower",
    "executor",
    "frontend",
    "celeryworker",
    "celerybeat",
    "169.254.169.254",
    "metadata.google.internal",
]
# The Docker host's public addresses and extra ranges a direct endpoint may never target
# (the container's default gateway is always blocked).
INFERENCE_HOST_PUBLIC_IPS = env.list("INFERENCE_HOST_PUBLIC_IPS", default=[])
INFERENCE_EXTRA_BLOCKED_CIDRS = env.list("INFERENCE_EXTRA_BLOCKED_CIDRS", default=[])
# Public base URL the relay install commands point at (empty: the request's scheme and host).
LLMLAB_PUBLIC_URL = env.str("LLMLAB_PUBLIC_URL", default="")
LLM_REQUEST_RETENTION_DAYS = 7

# TEMPLATES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#templates
TEMPLATES = [
    {
        # https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-TEMPLATES-BACKEND
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # https://docs.djangoproject.com/en/dev/ref/settings/#dirs
        "DIRS": [str(APPS_DIR / "templates")],
        # https://docs.djangoproject.com/en/dev/ref/settings/#app-dirs
        "APP_DIRS": True,
        "OPTIONS": {
            # https://docs.djangoproject.com/en/dev/ref/settings/#template-context-processors
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.i18n",
                "django.template.context_processors.media",
                "django.template.context_processors.static",
                "django.template.context_processors.tz",
                "django.contrib.messages.context_processors.messages",
                "backend.users.context_processors.allauth_settings",
            ],
        },
    },
]

# https://docs.djangoproject.com/en/dev/ref/settings/#form-renderer
FORM_RENDERER = "django.forms.renderers.TemplatesSetting"

# FIXTURES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#fixture-dirs
FIXTURE_DIRS = (str(APPS_DIR / "fixtures"),)

# SECURITY
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#session-cookie-httponly
SESSION_COOKIE_HTTPONLY = True
# Long-lived sessions when "Stay logged in" is checked (see AccountAdapter.login).
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30  # 30 days
# https://docs.djangoproject.com/en/dev/ref/settings/#csrf-cookie-httponly
# Must be False so the SvelteKit frontend can read the token from document.cookie
# and send it in the X-CSRFToken header for API requests.
CSRF_COOKIE_HTTPONLY = False
# https://docs.djangoproject.com/en/dev/ref/settings/#x-frame-options
X_FRAME_OPTIONS = "DENY"

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#email-timeout
EMAIL_TIMEOUT = 5

# ADMIN
# ------------------------------------------------------------------------------
# Django Admin URL.
ADMIN_URL = "admin/"
# https://docs.djangoproject.com/en/dev/ref/settings/#admins
ADMINS = ['"Marcin Grabowski" <marcin27059@gmail.com>']
# https://docs.djangoproject.com/en/dev/ref/settings/#managers
MANAGERS = ADMINS
# https://cookiecutter-django.readthedocs.io/en/latest/settings.html#other-environment-settings
# Force the `admin` sign in process to go through the `django-allauth` workflow
DJANGO_ADMIN_FORCE_ALLAUTH = env.bool("DJANGO_ADMIN_FORCE_ALLAUTH", default=False)

# LOGGING
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#logging
# See https://docs.djangoproject.com/en/dev/topics/logging for
# more details on how to customize your logging configuration.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(module)s %(process)d %(thread)d %(message)s",
        },
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
}

REDIS_URL = env("REDIS_URL", default="redis://redis:6379/0")
REDIS_SSL = REDIS_URL.startswith("rediss://")

# Celery
# ------------------------------------------------------------------------------
if USE_TZ:
    # https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-timezone
    CELERY_TIMEZONE = TIME_ZONE
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-broker_url
CELERY_BROKER_URL = REDIS_URL
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#redis-backend-use-ssl
CELERY_BROKER_USE_SSL = {"ssl_cert_reqs": ssl.CERT_NONE} if REDIS_SSL else None
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-result_backend
CELERY_RESULT_BACKEND = REDIS_URL
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#redis-backend-use-ssl
CELERY_REDIS_BACKEND_USE_SSL = CELERY_BROKER_USE_SSL
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#result-extended
CELERY_RESULT_EXTENDED = True
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#result-backend-always-retry
# https://github.com/celery/celery/pull/6122
CELERY_RESULT_BACKEND_ALWAYS_RETRY = True
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#result-backend-max-retries
CELERY_RESULT_BACKEND_MAX_RETRIES = 10
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-accept_content
CELERY_ACCEPT_CONTENT = ["json"]
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-task_serializer
CELERY_TASK_SERIALIZER = "json"
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-result_serializer
CELERY_RESULT_SERIALIZER = "json"
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-time-limit
# TODO: set to whatever value is adequate in your circumstances
CELERY_TASK_TIME_LIMIT = 5 * 60
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-soft-time-limit
# TODO: set to whatever value is adequate in your circumstances
CELERY_TASK_SOFT_TIME_LIMIT = 60
# https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html#visibility-timeout
# Redis hands an unacknowledged message to another worker once the visibility
# timeout passes (default 1 h). ``acks_late`` tasks acknowledge only when they
# finish and legitimately run for hours (``analysis.process_run`` up to
# ANALYSIS_TASK_SOFT_TIME_LIMIT + 10 min, 4 h 10 min by default;
# ``automation.run_pipeline_task`` up to 12 h 10 min), so with the default a
# healthy long task would be started a second time. Keep this above the hard
# time limit of every acks_late task. The cost: a message whose worker died is
# redelivered only this late; analysis does not wait for that, ``reconcile``
# requeues a run once its heartbeat is ANALYSIS_STALE_SECONDS old.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 13 * 3600}
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#beat-scheduler
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#worker-send-task-events
CELERY_WORKER_SEND_TASK_EVENTS = True
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std-setting-task_send_sent_event
CELERY_TASK_SEND_SENT_EVENT = True
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#worker-hijack-root-logger
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
# Prefork pool size. Unset = os.cpu_count() (the host's, not the container's
# cpus quota); local compose sets 2 to fit celeryworker's 1g cap.
CELERY_WORKER_CONCURRENCY = env.int("CELERY_WORKER_CONCURRENCY", default=None)
# django-allauth
# ------------------------------------------------------------------------------
ACCOUNT_ALLOW_REGISTRATION = env.bool("DJANGO_ACCOUNT_ALLOW_REGISTRATION", True)
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_LOGIN_METHODS = {"email"}
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_ADAPTER = "backend.users.adapters.AccountAdapter"
# https://docs.allauth.org/en/latest/account/forms.html
ACCOUNT_FORMS = {"signup": "backend.users.forms.UserSignupForm"}
# https://docs.allauth.org/en/latest/socialaccount/configuration.html
SOCIALACCOUNT_ADAPTER = "backend.users.adapters.SocialAccountAdapter"
# https://docs.allauth.org/en/latest/socialaccount/configuration.html
SOCIALACCOUNT_FORMS = {"signup": "backend.users.forms.UserSocialSignupForm"}

# django-allauth headless
# ------------------------------------------------------------------------------
HEADLESS_ONLY = True
# HEADLESS_FRONTEND_URLS is set below (after DJANGO_DOMAIN / FRONTEND_PUBLIC_ORIGIN).


def build_headless_frontend_urls(origin: str) -> dict[str, str]:
    """Absolute URLs for links in allauth emails (required for headless mode)."""
    base = origin.rstrip("/")
    return {
        "account_confirm_email": f"{base}/auth/verify-email/{{key}}",
        "account_reset_password": f"{base}/auth/password/reset",
        "account_reset_password_from_key": f"{base}/auth/password/reset/{{key}}",
        "account_signup": f"{base}/auth/signup",
    }


# Your stuff...
# ------------------------------------------------------------------------------
# Public domain for this deployment. Drives the django_sites entry and the
# `HTTP-Referer` we send to OpenRouter. Override in env per environment.
DJANGO_DOMAIN = env("DJANGO_DOMAIN", default="example.com")
SITE_NAME = env("SITE_NAME", default="LLM Eval Lab")

# Browser-reachable origin for the SvelteKit app (email links, redirects).
# Must not be an internal Docker hostname (e.g. django:8000).
FRONTEND_PUBLIC_ORIGIN = env("FRONTEND_PUBLIC_ORIGIN", default="").strip().rstrip("/")
HEADLESS_FRONTEND_URLS = build_headless_frontend_urls(
    FRONTEND_PUBLIC_ORIGIN or f"https://{DJANGO_DOMAIN}",
)

OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", default="")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_HTTP_REFERER = env(
    "OPENROUTER_HTTP_REFERER",
    default=f"https://{DJANGO_DOMAIN}",
)
# When True, generation jobs whose users haven't configured a personal
# OpenRouter API key fall back to ``OPENROUTER_API_KEY``. When False, those
# jobs are rejected at submission with a "configure your key" error.
OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK = env.bool(
    "OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK",
    default=True,
)

# Hostname used to build public URLs for generated sample-app containers.
# Only used when TRAEFIK_DYNAMIC_DIR is NOT set (local dev / non-Traefik).
CONTAINER_APP_HOST = env("CONTAINER_APP_HOST", default="")

# Base domain for sample-app subdomains in bridge mode.  Each app is reached at
# https://<container_name>.<APPS_DOMAIN> through Traefik (TLS via the wildcard
# *.<APPS_DOMAIN> cert).  Defaults to DJANGO_DOMAIN; a dedicated subdomain such
# as "apps.example.com" is recommended so the wildcard stays scoped.
APPS_DOMAIN = env("APPS_DOMAIN", default=DJANGO_DOMAIN)

# Subdomain routing via a Django reverse-proxy (for envs without Traefik, e.g. a
# single Caddy fronting one Django).  When True, a wildcard ``*.<APPS_DOMAIN>``
# edge route forwards to Django, and AppSubdomainProxyMiddleware serves the app
# at the subdomain root so /assets and /api resolve to the app.  Needs wildcard
# DNS + a wildcard TLS cert at the edge.
APPS_SUBDOMAIN_PROXY = env.bool("APPS_SUBDOMAIN_PROXY", default=False)

# Path-based routing (no wildcard DNS/cert needed).  When True, sample apps are
# reached at <APPS_PUBLIC_ORIGIN>/apps/<container_name>/ and Django reverse-
# proxies that path to the app container.  NOTE: breaks SPAs that use
# root-absolute /assets or /api (they collide with the main platform) — prefer
# APPS_SUBDOMAIN_PROXY.  Mutually exclusive with the Traefik bridge.
APPS_PROXY_PATH = env.bool("APPS_PROXY_PATH", default=False)
# Browser-facing origin that fronts /apps/ (falls back to FRONTEND_PUBLIC_ORIGIN
# then https://DJANGO_DOMAIN).
APPS_PUBLIC_ORIGIN = env("APPS_PUBLIC_ORIGIN", default="").strip().rstrip("/")
# Host Django uses to reach an app's published port when the app binds a host
# port (dev). Requires the django container to resolve it — docker-compose adds
# host.docker.internal:host-gateway.  Ignored when apps run on DOCKER_APPS_NETWORK
# (then Django reaches them by container name).
APPS_UPSTREAM_HOST = env("APPS_UPSTREAM_HOST", default="host.docker.internal")

# When set, Django writes per-container Traefik dynamic route files here so
# Traefik can terminate TLS on each app's dedicated port.  Must be the same
# path mounted into the Traefik container as /etc/traefik/dynamic/.
TRAEFIK_DYNAMIC_DIR = env("TRAEFIK_DYNAMIC_DIR", default="")

# Docker network for sample-app containers.  When set, containers are spawned
# on this network WITHOUT host port binding; Traefik (also on the network)
# reaches them by container name at port 8000.
DOCKER_APPS_NETWORK = env("DOCKER_APPS_NETWORK", default="")

# Docker daemon that builds and runs sample apps and analyzer workspaces.  Empty
# means the standard DOCKER_HOST/socket environment.  Local compose points it at
# the apps-dind sandbox (tcp://apps-dind:2376), so generated apps run as nested
# containers and never touch the host daemon.  APPS_DOCKER_CERT_PATH holds the
# mutual-TLS client cert (ca.pem, cert.pem, key.pem) for that daemon.
APPS_DOCKER_HOST = env("APPS_DOCKER_HOST", default="")
APPS_DOCKER_CERT_PATH = env("APPS_DOCKER_CERT_PATH", default="")
# Local-model (non-OpenRouter) agent runs: their gateway reaches the executor's
# LLM proxy only through apps-dind's llmproxy attachment + firewall exception
# (compose/local/apps-dind/entrypoint.sh). Without apps-dind (production compose,
# host daemon) that path does not exist, so such runs fail fast instead of
# burning infra retries. Scaffolding runs on local models are unaffected.
LLM_LOCAL_AGENT_RUNS = env.bool("LLM_LOCAL_AGENT_RUNS", default=bool(APPS_DOCKER_HOST))

# ── Analysis engine (backend/analysis/engine) ───────────────────────
# Runs execute as a durable DB state machine (lease + heartbeat) on Celery
# (``analysis.process_run``), or on a thread when no worker answers a ping.
# Runs analysed at once across every process (Postgres advisory-lock slots).
ANALYSIS_MAX_CONCURRENT_RUNS = env.int("ANALYSIS_MAX_CONCURRENT_RUNS", default=2)
# Memory the tool containers may reserve on the apps daemon, counting every
# running container there (sample apps, agent sandboxes, other tools): the
# daemon reports the host's RAM, not the 4 GB cap of apps-dind.
ANALYSIS_DIND_MEMORY_BUDGET = env.str("ANALYSIS_DIND_MEMORY_BUDGET", default="3500m")
# A RUNNING run whose heartbeat is older than this is presumed dead and requeued.
ANALYSIS_STALE_SECONDS = env.int("ANALYSIS_STALE_SECONDS", default=180)
ANALYSIS_HEARTBEAT_SECONDS = env.int("ANALYSIS_HEARTBEAT_SECONDS", default=15)
# Executions of one run / one tool before an infrastructure failure is final.
ANALYSIS_MAX_ATTEMPTS = env.int("ANALYSIS_MAX_ATTEMPTS", default=3)
# How long a tool may wait for daemon memory before it fails as infra_error.
ANALYSIS_CAPACITY_WAIT_SECONDS = env.int("ANALYSIS_CAPACITY_WAIT_SECONDS", default=1800)
ANALYSIS_USE_CELERY = env.bool("ANALYSIS_USE_CELERY", default=True)
# Ceiling of one analysis.process_run task (the sum of tool budgets is far lower).
# Its hard limit (+600 s) must stay below the broker's visibility timeout
# (CELERY_BROKER_TRANSPORT_OPTIONS), or Redis redelivers a run still in progress.
ANALYSIS_TASK_SOFT_TIME_LIMIT = env.int("ANALYSIS_TASK_SOFT_TIME_LIMIT", default=4 * 3600)
ANALYSIS_DEFAULT_REVIEW_MODEL = env.str("ANALYSIS_DEFAULT_REVIEW_MODEL", default="deepseek/deepseek-v4.1-flash")
ANALYSIS_REVIEW_SANDBOX_MEM_LIMIT = env.str("ANALYSIS_REVIEW_SANDBOX_MEM_LIMIT", default="1g")
# Raw tool outputs / review transcripts: <root>/<this>/<run id>-<HMAC of the run id>/ (gzip + sha256),
# served only by the owner-scoped artifact API. <root> is ANALYSIS_ARTIFACTS_ROOT when set: point it at a
# persistent directory the web server does not serve. Empty = MEDIA_ROOT (publicly served at /media/; the
# HMAC in the directory name keeps a known run id from naming a file there). docker-compose.production.yml
# sets it to the private production_analysis_artifacts volume; local dev keeps MEDIA_ROOT.
ANALYSIS_ARTIFACTS_DIR = env.str("ANALYSIS_ARTIFACTS_DIR", default="analysis_runs")
ANALYSIS_ARTIFACTS_ROOT = env.str("ANALYSIS_ARTIFACTS_ROOT", default="")
# Per tool container limits (memory and timeout come from the tool spec).
ANALYSIS_TOOL_NANO_CPUS = env.int("ANALYSIS_TOOL_NANO_CPUS", default=1_000_000_000)
ANALYSIS_TOOL_PIDS_LIMIT = env.int("ANALYSIS_TOOL_PIDS_LIMIT", default=512)
ANALYSIS_TOOL_TMPFS_SIZE = env.str("ANALYSIS_TOOL_TMPFS_SIZE", default="512m")
# Bytes kept of a tool's stdout and of its stderr (each), and of its /out directory.
ANALYSIS_OUTPUT_CAP_BYTES = env.int("ANALYSIS_OUTPUT_CAP_BYTES", default=16 * 1024 * 1024)
ANALYSIS_OUT_DIR_CAP_BYTES = env.int("ANALYSIS_OUT_DIR_CAP_BYTES", default=128 * 1024 * 1024)
# Build the analyzer image when a run needs it and it is missing (e.g. pruned).
ANALYSIS_BUILD_IMAGE_ON_DEMAND = env.bool("ANALYSIS_BUILD_IMAGE_ON_DEMAND", default=True)
# Run the lease heartbeat on its own thread (tests run it inline instead).
ANALYSIS_HEARTBEAT_THREAD = env.bool("ANALYSIS_HEARTBEAT_THREAD", default=True)

# ── User-authored stacks ─────────────────────────────────────────────
# User stacks never ship their own Dockerfile: it is generated server-side
# from a pinned template, and base images must come from this allowlist.
STACK_ALLOWED_BASE_IMAGES = {
    "python": env.list(
        "STACK_ALLOWED_PYTHON_IMAGES",
        default=["python:3.11-slim", "python:3.12-slim"],
    ),
    "node": env.list(
        "STACK_ALLOWED_NODE_IMAGES",
        default=["node:20-alpine", "node:22-alpine"],
    ),
}
# When True, non-staff stacks need staff approval before jobs can use them.
# Off by default: single-user research installs.
STACK_REQUIRE_APPROVAL = env.bool("STACK_REQUIRE_APPROVAL", default=False)
