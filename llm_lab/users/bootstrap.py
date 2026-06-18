from django.conf import settings

from llm_lab.users.models import User


def requires_bootstrap_admin() -> bool:
    return not User.objects.exists()


def default_bootstrap_admin_email() -> str:
    domain = settings.DJANGO_DOMAIN.strip() or "localhost"
    return f"admin@{domain}"


def resolve_bootstrap_admin_email(email: str | None) -> str:
    candidate = (email or "").strip()
    if candidate:
        return User.objects.normalize_email(candidate)
    return User.objects.normalize_email(default_bootstrap_admin_email())
