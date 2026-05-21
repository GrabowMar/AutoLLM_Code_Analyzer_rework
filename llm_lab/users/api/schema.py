from django.urls import reverse
from ninja import ModelSchema

from llm_lab.users.models import User


class UpdateUserSchema(ModelSchema):
    class Meta:
        model = User
        fields = ["name"]


class UserSchema(ModelSchema):
    url: str
    is_staff: bool

    class Meta:
        model = User
        fields = ["email", "name"]

    @staticmethod
    def resolve_url(obj: User):
        return reverse("api:retrieve_user", kwargs={"pk": obj.pk})

    @staticmethod
    def resolve_is_staff(obj: User) -> bool:
        return obj.is_staff
