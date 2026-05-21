from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from ninja import Router

from llm_lab.users.api.schema import UpdateUserSchema
from llm_lab.users.api.schema import UserSchema
from llm_lab.users.models import User

router = Router(tags=["users"])


def _get_users_queryset(request) -> QuerySet[User]:
    return User.objects.filter(pk=request.auth.pk)


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
