"""Tests for GenerationService — validates copilot iteration and scaffolding flow."""

from unittest.mock import MagicMock

from llm_lab.generation.services.generation_service import GenerationService


class TestValidatePythonCode:
    """Tests for _validate_python_code static method."""

    def test_valid_code(self) -> None:
        lines = [
            "from flask import Flask, request, jsonify",
            "app = Flask(__name__)",
            "",
        ]
        for i in range(30):
            lines.extend(
                [
                    f"@app.route('/item{i}')",
                    f"def item_{i}():",
                    f"    return jsonify(ok={i})",
                    "",
                ],
            )
        code = "\n".join(lines)
        errors = GenerationService._validate_python_code(code)
        assert len(errors) == 0

    def test_syntax_error(self) -> None:
        code = "def broken(\n    return None"
        errors = GenerationService._validate_python_code(code)
        assert len(errors) >= 1
        assert any("SyntaxError" in e for e in errors)

    def test_empty_code(self) -> None:
        errors = GenerationService._validate_python_code("")
        assert any("Empty" in e for e in errors)

    def test_too_short(self) -> None:
        code = "x = 1\ny = 2\n"
        errors = GenerationService._validate_python_code(code)
        assert any("too short" in e.lower() for e in errors)

    def test_stub_functions(self) -> None:
        code = "\n".join(
            [
                "def f1(x):",
                "    pass",
                "",
                "def f2(x):",
                "    pass",
                "",
                "def f3(x):",
                "    pass",
                "",
                "def f4(x):",
                "    pass",
            ],
        )
        errors = GenerationService._validate_python_code(code)
        assert any("stub" in e.lower() for e in errors)

    def test_real_flask_app(self) -> None:
        """A realistic 100+ line Flask app should pass validation."""
        lines = [
            "from flask import Flask, request, jsonify",
            "from flask_sqlalchemy import SQLAlchemy",
            "from flask_cors import CORS",
            "",
            "app = Flask(__name__)",
            "CORS(app)",
            "app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'",
            "db = SQLAlchemy(app)",
            "",
            "class Task(db.Model):",
            "    id = db.Column(db.Integer, primary_key=True)",
            "    title = db.Column(db.String(200))",
            "    done = db.Column(db.Boolean, default=False)",
            "",
            "    def to_dict(self):",
            "        return {'id': self.id, 'title': self.title, 'done': self.done}",
            "",
        ]
        # Pad to 100+ lines with routes
        for i in range(20):
            lines.extend(
                [
                    f"@app.route('/api/item{i}', methods=['GET'])",
                    f"def get_item_{i}():",
                    f"    return jsonify({{'item': {i}}})",
                    "",
                ],
            )
        code = "\n".join(lines)
        assert len(code.splitlines()) > 30
        errors = GenerationService._validate_python_code(code)
        assert len(errors) == 0


class TestPickCopilotModel:
    """Tests for _pick_copilot_model static method."""

    def test_explicit_model(self) -> None:
        job = MagicMock()
        job.model = MagicMock()
        job.model.model_id = "openai/gpt-4o"
        result = GenerationService._pick_copilot_model(job)
        assert result == "openai/gpt-4o"

    def test_open_source_default(self) -> None:
        job = MagicMock()
        job.model = None
        job.copilot_use_open_source = True
        result = GenerationService._pick_copilot_model(job)
        assert "deepseek" in result

    def test_closed_source_default(self) -> None:
        job = MagicMock()
        job.model = None
        job.copilot_use_open_source = False
        result = GenerationService._pick_copilot_model(job)
        assert "gpt-4o-mini" in result
