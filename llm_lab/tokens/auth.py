from __future__ import annotations

from ninja.security import HttpBearer

from llm_lab.tokens import services


class TokenAuth(HttpBearer):
    def authenticate(self, request, token):
        api_token = services.verify_token(token)
        if api_token is None:
            return None
        ip = request.META.get("REMOTE_ADDR", "")
        api_token.mark_used(ip)
        return api_token.user
