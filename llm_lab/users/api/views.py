from allauth.account.adapter import get_adapter
from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError

from llm_lab.users.api.schema import BootstrapAdminCreateSchema
from llm_lab.users.api.schema import BootstrapStatusSchema
from llm_lab.users.api.schema import UpdateUserSchema
from llm_lab.users.api.schema import UserSchema
from llm_lab.users.bootstrap import default_bootstrap_admin_email
from llm_lab.users.bootstrap import requires_bootstrap_admin
from llm_lab.users.bootstrap import resolve_bootstrap_admin_email
from llm_lab.users.models import User

router = Router(tags=["users"])


def _get_users_queryset(request) -> QuerySet[User]:
    return User.objects.filter(pk=request.auth.pk)


@router.get("/bootstrap-status/", response=BootstrapStatusSchema, auth=None)
def bootstrap_status(request):
    return {
        "requires_bootstrap": requires_bootstrap_admin(),
        "default_email": default_bootstrap_admin_email(),
    }


@router.post("/bootstrap-admin/", response=UserSchema, auth=None)
def create_bootstrap_admin(request, data: BootstrapAdminCreateSchema):
    if not requires_bootstrap_admin():
        raise HttpError(409, "Bootstrap admin already exists.")

    user_name = data.name.strip() or "Admin"
    email = resolve_bootstrap_admin_email(data.email)
    user_model = get_user_model()
    user = user_model.objects.create_superuser(
        email=email,
        password=data.password,
        name=user_name,
        is_active=True,
    )
    request._remember_me = data.remember
    get_adapter(request).login(request, user)
    return user


@router.get("/", response=list[UserSchema])
def list_users(request):
    return _get_users_queryset(request)


@router.get("/me/", response=UserSchema)
def retrieve_current_user(request):
    return request.auth


@router.get("/{pk}/", response=UserSchema)
def retrieve_user(request, pk: int):
    users_qs = _get_users_queryset(request)
    return get_object_or_404(users_qs, pk=pk)


@router.patch("/me/", response=UserSchema)
def update_current_user(request, data: UpdateUserSchema):
    user = request.auth
    user.name = data.name
    user.save()
    return user


@router.patch("/{pk}/", response=UserSchema)
def update_user(request, pk: int, data: UpdateUserSchema):
    users_qs = _get_users_queryset(request)
    user = get_object_or_404(users_qs, pk=pk)
    user.name = data.name
    user.save()
    return user
