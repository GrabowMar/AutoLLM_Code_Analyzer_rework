"""Backend Scanner — extracts API structure from generated backend code.

Scans generated Flask/Python backend code to extract endpoints, models, and auth info.
This context feeds into frontend prompt rendering for two-stage scaffolding generation.
Ported from ThesisAppRework/src/app/services/generation_v2/backend_scanner.py.
"""

import logging
import re
from dataclasses import dataclass
from dataclasses import field

logger = logging.getLogger(__name__)


@dataclass
class EndpointInfo:
    """Information about an API endpoint."""

    method: str
    path: str
    blueprint: str  # user, admin, auth
    requires_auth: bool = False
    requires_admin: bool = False
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "path": self.path,
            "blueprint": self.blueprint,
            "requires_auth": self.requires_auth,
            "requires_admin": self.requires_admin,
        }


@dataclass
class ModelInfo:
    """Information about a database model."""

    name: str
    fields: list[str] = field(default_factory=list)
    has_to_dict: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "fields": self.fields,
            "has_to_dict": self.has_to_dict,
        }


@dataclass
class BackendScanResult:
    """Result of scanning backend code."""

    endpoints: list[EndpointInfo] = field(default_factory=list)
    models: list[ModelInfo] = field(default_factory=list)
    has_auth: bool = False
    has_admin: bool = False

    def to_dict(self) -> dict:
        return {
            "endpoints": [e.to_dict() for e in self.endpoints],
            "models": [m.to_dict() for m in self.models],
            "has_auth": self.has_auth,
            "has_admin": self.has_admin,
        }

    def to_frontend_context(self) -> str:
        """Generate minimal context string for frontend prompt."""
        lines = []

        auth_endpoints = [e for e in self.endpoints if e.blueprint == "auth"]
        user_endpoints = [e for e in self.endpoints if e.blueprint == "user"]
        admin_endpoints = [e for e in self.endpoints if e.blueprint == "admin"]

        if auth_endpoints:
            lines.append("## Auth Endpoints")
            for e in auth_endpoints:
                lines.append(f"- {e.method} {e.path}")

        if user_endpoints:
            lines.append("\n## User API Endpoints")
            for e in user_endpoints:
                auth_note = " (auth required)" if e.requires_auth else ""
                lines.append(f"- {e.method} {e.path}{auth_note}")

        if admin_endpoints:
            lines.append("\n## Admin API Endpoints (admin required)")
            for e in admin_endpoints:
                lines.append(f"- {e.method} {e.path}")

        if self.models:
            lines.append("\n## Data Models")
            for m in self.models:
                if m.name != "User":
                    fields_str = ", ".join(m.fields[:5])
                    if len(m.fields) > 5:
                        fields_str += "..."
                    lines.append(f"- {m.name}: {fields_str}")

        return "\n".join(lines)


class BackendScanner:
    """Scans generated backend code to extract API structure."""

    def scan(self, backend_code: dict[str, str] | str) -> BackendScanResult:
        """Scan backend code and extract API info."""
        result = BackendScanResult()

        if isinstance(backend_code, dict):
            all_code = "\n\n".join(backend_code.values())
        else:
            all_code = str(backend_code)

        result.models = self._extract_models(all_code)
        result.endpoints = self._extract_endpoints(all_code)
        result.has_auth = any(e.path.startswith("/api/auth") for e in result.endpoints)
        result.has_admin = any(e.path.startswith("/api/admin") for e in result.endpoints)

        logger.info(
            "Scanned backend: %d endpoints, %d models",
            len(result.endpoints),
            len(result.models),
        )
        return result

    def scan_raw_response(self, raw_response: str) -> BackendScanResult:
        """Scan raw LLM response text containing code blocks."""
        code_dict = self._extract_code_blocks(raw_response)
        if code_dict:
            return self.scan(code_dict)
        return self.scan(raw_response)

    def _extract_code_blocks(self, content: str) -> dict[str, str]:
        """Extract annotated code blocks from LLM response."""
        blocks: dict[str, str] = {}
        pattern = re.compile(
            r"```(?:python)(?::(?P<filename>[^\n\r`]+))?\s*[\r\n]+(.*?)```",
            re.DOTALL,
        )
        for match in pattern.finditer(content or ""):
            filename = (match.group("filename") or "main").strip().lower()
            code = (match.group(2) or "").strip()
            if code:
                key = filename.replace(".py", "").replace("routes/", "")
                blocks[key] = code
        return blocks

    def _extract_models(self, code: str) -> list[ModelInfo]:
        """Extract SQLAlchemy model class definitions."""
        models = []
        class_pattern = re.compile(
            r"class\s+(\w+)\s*\(\s*db\.Model\s*\)\s*:(.+?)(?=\nclass\s|\Z)",
            re.DOTALL,
        )
        for match in class_pattern.finditer(code):
            name = match.group(1)
            body = match.group(2)
            fields = re.findall(r"(\w+)\s*=\s*db\.Column\s*\(", body)
            has_to_dict = "def to_dict" in body
            models.append(ModelInfo(name=name, fields=fields, has_to_dict=has_to_dict))
        return models

    def _extract_endpoints(self, code: str) -> list[EndpointInfo]:
        """Extract Flask route definitions."""
        endpoints = []
        route_pattern = re.compile(
            r"@(\w+)\.route\s*\(\s*['\"]([^'\"]+)['\"]"
            r"(?:\s*,\s*methods\s*=\s*\[([^\]]+)\])?\s*\)",
            re.MULTILINE,
        )

        for match in route_pattern.finditer(code):
            route_obj = match.group(1)
            path = match.group(2)
            methods_str = match.group(3)

            if "/admin" in path or "admin" in route_obj:
                blueprint = "admin"
            elif "/auth" in path or "auth" in route_obj:
                blueprint = "auth"
            else:
                blueprint = "user"

            if methods_str:
                methods = [m.strip().strip("'\"") for m in methods_str.split(",")]
            else:
                methods = ["GET"]

            start = max(0, match.start() - 200)
            end = min(len(code), match.end() + 100)
            context = code[start:end]
            requires_auth = bool(
                re.search(r"@token_required|@login_required", context),
            )
            requires_admin = bool(re.search(r"@admin_required", context)) or blueprint == "admin"

            if not path.startswith("/api"):
                prefix = "/api/admin" if blueprint == "admin" else ("/api/auth" if blueprint == "auth" else "/api")
                path = f"{prefix}{path}" if path.startswith("/") else f"{prefix}/{path}"

            for method in methods:
                endpoints.append(
                    EndpointInfo(
                        method=method.upper(),
                        path=path,
                        blueprint=blueprint,
                        requires_auth=requires_auth or requires_admin,
                        requires_admin=requires_admin,
                    ),
                )

        return endpoints


def scan_backend_code(code: dict[str, str] | str) -> BackendScanResult:
    """Convenience function to scan backend code."""
    return BackendScanner().scan(code)


def scan_backend_response(raw_response: str) -> BackendScanResult:
    """Convenience function to scan raw LLM response."""
    return BackendScanner().scan_raw_response(raw_response)
