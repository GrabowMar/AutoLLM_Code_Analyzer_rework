"""Exercise the browser signup, confirmation email and verification flow."""

import re
from urllib.parse import unquote

import pytest
from allauth.account.models import EmailAddress
from django.test import Client

from backend.users.models import User
from config.settings.base import build_headless_frontend_urls

pytestmark = pytest.mark.django_db

BASE = "/_allauth/browser/v1"
ORIGIN = "https://lab.example.org"
EMAIL = "signup@example.org"
PASSWORD = "A-long-signup-password-472!"  # noqa: S105 - test credential


@pytest.fixture
def browser(settings):
    settings.ACCOUNT_ALLOW_REGISTRATION = True
    settings.ACCOUNT_EMAIL_VERIFICATION = "mandatory"
    settings.ACCOUNT_RATE_LIMITS = False
    settings.ALLOWED_HOSTS = ["lab.example.org"]
    settings.HEADLESS_FRONTEND_URLS = build_headless_frontend_urls(ORIGIN)
    return Client(enforce_csrf_checks=True, headers={"host": "lab.example.org"})


def post(browser, settings, path, data):
    browser.get(f"{BASE}/config", secure=True)
    return browser.post(
        f"{BASE}{path}",
        data=data,
        content_type="application/json",
        secure=True,
        HTTP_ORIGIN=ORIGIN,
        HTTP_X_CSRFTOKEN=browser.cookies[settings.CSRF_COOKIE_NAME].value,
    )


def test_signup_sends_confirmation_link_and_requires_verification(browser, settings, mailoutbox):
    response = post(browser, settings, "/auth/signup", {"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 401
    assert {"id": "verify_email", "is_pending": True} in response.json()["data"]["flows"]
    address = EmailAddress.objects.get(email=EMAIL)
    assert not address.verified
    assert "_auth_user_id" not in browser.session
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [EMAIL]
    link = re.search(rf"{re.escape(ORIGIN)}/auth/verify-email/([^\s]+)", mailoutbox[0].body)
    assert link, mailoutbox[0].body

    response = post(browser, settings, "/auth/login", {"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 401
    assert "_auth_user_id" not in browser.session

    # The emailed link also works when opened in a different browser session.
    other_browser = Client(enforce_csrf_checks=True, headers={"host": "lab.example.org"})
    response = post(other_browser, settings, "/auth/email/verify", {"key": unquote(link.group(1))})
    assert response.status_code in (200, 401), response.json()
    address.refresh_from_db()
    assert address.verified

    response = post(other_browser, settings, "/auth/login", {"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["data"]["user"]["email"] == EMAIL
    assert other_browser.session["_auth_user_id"] == str(address.user_id)


def test_closed_registration_rejects_signup_without_sending_email(browser, settings, mailoutbox):
    settings.ACCOUNT_ALLOW_REGISTRATION = False
    response = post(browser, settings, "/auth/signup", {"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 403
    assert response.json()["status"] == 403
    assert not User.objects.filter(email=EMAIL).exists()
    assert not mailoutbox


def test_signup_still_requires_csrf(browser, mailoutbox):
    response = browser.post(
        f"{BASE}/auth/signup",
        data={"email": EMAIL, "password": PASSWORD},
        content_type="application/json",
        secure=True,
        HTTP_ORIGIN=ORIGIN,
    )

    assert response.status_code == 403
    assert not User.objects.filter(email=EMAIL).exists()
    assert not mailoutbox
