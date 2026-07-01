"""Tests for backend scanner service."""

from backend.generation.services.backend_scanner import BackendScanner
from backend.generation.services.backend_scanner import scan_backend_code
from backend.generation.services.backend_scanner import scan_backend_response


class TestBackendScanner:
    """Tests for BackendScanner."""

    SAMPLE_FLASK_CODE = """\
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
db = SQLAlchemy(app)


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    priority = db.Column(db.Integer, default=1)
    completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=db.func.now())

    def to_dict(self):
        return {'id': self.id, 'title': self.title}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True)
    password_hash = db.Column(db.String(256))


@app.route('/api/auth/register', methods=['POST'])
def register():
    pass

@app.route('/api/auth/login', methods=['POST'])
def login():
    pass

@app.route('/api/tasks', methods=['GET', 'POST'])
@token_required
def tasks():
    pass

@app.route('/api/tasks/<int:task_id>', methods=['GET', 'PUT', 'DELETE'])
@token_required
def task_detail(task_id):
    pass

@app.route('/api/admin/stats', methods=['GET'])
@admin_required
def admin_stats():
    pass
"""

    def test_scan_extracts_models(self) -> None:
        scanner = BackendScanner()
        result = scanner.scan(self.SAMPLE_FLASK_CODE)

        assert len(result.models) == 2
        task_model = next(m for m in result.models if m.name == "Task")
        assert "title" in task_model.fields
        assert "priority" in task_model.fields
        assert task_model.has_to_dict is True

        user_model = next(m for m in result.models if m.name == "User")
        assert "email" in user_model.fields
        assert user_model.has_to_dict is False

    def test_scan_extracts_endpoints(self) -> None:
        scanner = BackendScanner()
        result = scanner.scan(self.SAMPLE_FLASK_CODE)

        assert len(result.endpoints) >= 7  # register, login, 2×tasks, 3×task_detail, admin
        paths = [e.path for e in result.endpoints]
        assert any("/api/auth/register" in p for p in paths)
        assert any("/api/auth/login" in p for p in paths)

    def test_scan_detects_auth(self) -> None:
        scanner = BackendScanner()
        result = scanner.scan(self.SAMPLE_FLASK_CODE)

        assert result.has_auth is True
        assert result.has_admin is True

    def test_scan_detects_auth_decorators(self) -> None:
        scanner = BackendScanner()
        result = scanner.scan(self.SAMPLE_FLASK_CODE)

        admin_endpoints = [e for e in result.endpoints if e.blueprint == "admin"]
        assert len(admin_endpoints) >= 1
        assert all(e.requires_admin for e in admin_endpoints)

    def test_scan_raw_response_with_code_blocks(self) -> None:
        raw = f"Here's the code:\n```python:app.py\n{self.SAMPLE_FLASK_CODE}\n```"
        result = scan_backend_response(raw)

        assert len(result.models) >= 1
        assert len(result.endpoints) >= 1

    def test_scan_dict(self) -> None:
        code_dict = {"app": self.SAMPLE_FLASK_CODE}
        result = scan_backend_code(code_dict)

        assert len(result.models) == 2
        assert len(result.endpoints) >= 7

    def test_to_frontend_context(self) -> None:
        scanner = BackendScanner()
        result = scanner.scan(self.SAMPLE_FLASK_CODE)
        context = result.to_frontend_context()

        assert "Auth Endpoints" in context
        assert "User API Endpoints" in context
        assert "Admin API Endpoints" in context
        assert "Data Models" in context
        assert "Task" in context

    def test_to_dict(self) -> None:
        scanner = BackendScanner()
        result = scanner.scan(self.SAMPLE_FLASK_CODE)
        d = result.to_dict()

        assert isinstance(d["endpoints"], list)
        assert isinstance(d["models"], list)
        assert isinstance(d["has_auth"], bool)

    def test_empty_code(self) -> None:
        result = scan_backend_code("")
        assert len(result.endpoints) == 0
        assert len(result.models) == 0

    def test_no_routes(self) -> None:
        code = "x = 1\ny = 2\n"
        result = scan_backend_code(code)
        assert len(result.endpoints) == 0
