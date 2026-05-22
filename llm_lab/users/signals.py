import logging

from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver

from llm_lab.users.models import User

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def promote_first_user_or_marcin(sender, instance, created, **kwargs):
    """
    Automatically promotes:
    1. The first registered user in the database to superuser & staff (skipped in tests).
    2. Any new user registered with the email 'marcin27059@gmail.com' to superuser & staff.
    """
    if created:
        try:
            from django.conf import settings

            # Skip first-user auto-promotion in test environment to avoid promoting regular test fixtures
            is_testing = getattr(settings, "TESTING", False)
            is_first = not is_testing and (User.objects.count() == 1)
            is_marcin = instance.email == "marcin27059@gmail.com"

            if is_first or is_marcin:
                if not instance.is_staff or not instance.is_superuser:
                    instance.is_staff = True
                    instance.is_superuser = True
                    # Use update_fields to avoid triggering other post_save handlers recursively
                    instance.save(update_fields=["is_staff", "is_superuser"])
        except Exception:
            # Prevent signup failures if anything goes wrong during auto-promotion
            logger.exception("Auto-promotion failed for %s", instance.email)


@receiver(user_logged_in)
def ensure_marcin_is_admin(sender, request, user, **kwargs):
    """
    Fail-safe: Ensures that if marcin27059@gmail.com logs in, they are staff and superuser.
    """
    if getattr(user, "email", None) == "marcin27059@gmail.com":
        try:
            if not user.is_staff or not user.is_superuser:
                user.is_staff = True
                user.is_superuser = True
                user.save(update_fields=["is_staff", "is_superuser"])
        except Exception:
            logger.exception("Fail-safe admin promotion failed for %s", user.email)
