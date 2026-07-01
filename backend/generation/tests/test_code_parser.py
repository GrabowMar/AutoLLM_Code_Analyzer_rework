"""Tests for code parser service."""

from backend.generation.services.code_parser import extract_code_blocks
from backend.generation.services.code_parser import extract_frontend_code
from backend.generation.services.code_parser import extract_python_code
from backend.generation.services.code_parser import infer_python_dependencies
from backend.generation.services.code_parser import parse_result_to_structured


class TestExtractCodeBlocks:
    """Tests for extract_code_blocks()."""

    def test_python_annotated(self) -> None:
        content = "```python:app.py\nfrom flask import Flask\napp = Flask(__name__)\n```"
        blocks = extract_code_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["language"] == "python"
        assert "app.py" in blocks[0]["filename"]
        assert "Flask" in blocks[0]["code"]

    def test_multiple_blocks(self) -> None:
        content = "```python:app.py\nimport flask\n```\n\n```jsx:App.jsx\nexport default function App() {}\n```"
        blocks = extract_code_blocks(content)
        assert len(blocks) == 2
        assert blocks[0]["language"] == "python"
        assert blocks[1]["language"] == "jsx"

    def test_empty_content(self) -> None:
        assert extract_code_blocks("") == []
        assert extract_code_blocks(None) == []

    def test_no_code_blocks(self) -> None:
        assert extract_code_blocks("Just some plain text") == []

    def test_unannotated_block(self) -> None:
        content = "```python\nprint('hello')\n```"
        blocks = extract_code_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["code"] == "print('hello')"

    def test_inline_filename_marker_stripped(self) -> None:
        # Some models put the filename as a bare ":app.py" line inside the block.
        content = "```python\n:app.py\nimport os\nprint('hi')\n```"
        blocks = extract_code_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["filename"] == "app.py"
        assert blocks[0]["code"].splitlines()[0] == "import os"
        assert ":app.py" not in blocks[0]["code"]

    def test_inline_filename_marker_not_confused_with_code(self) -> None:
        # A legit line starting with ':' (no filename) must NOT be stripped.
        content = "```python\nx = {1: 2}\n```"
        blocks = extract_code_blocks(content)
        assert blocks[0]["code"] == "x = {1: 2}"


class TestExtractPythonCode:
    """Tests for extract_python_code()."""

    def test_single_block(self) -> None:
        content = "```python:app.py\nfrom flask import Flask\napp = Flask(__name__)\n```"
        code = extract_python_code(content)
        assert "Flask" in code

    def test_merge_multiple_blocks(self) -> None:
        content = (
            "```python:models.py\n"
            "from flask_sqlalchemy import SQLAlchemy\n"
            "db = SQLAlchemy()\n"
            "class User(db.Model):\n"
            "    id = db.Column(db.Integer, primary_key=True)\n"
            "```\n\n"
            "```python:routes.py\n"
            "from flask import Flask\n"
            "@app.route('/users')\n"
            "def users(): pass\n"
            "```"
        )
        code = extract_python_code(content)
        # Should have merged imports
        assert "flask_sqlalchemy" in code or "SQLAlchemy" in code
        assert "Flask" in code or "@app.route" in code

    def test_requirements_excluded(self) -> None:
        content = (
            "```requirements:requirements.txt\nflask\nflask-cors\n```\n\n"
            "```python:app.py\nfrom flask import Flask\n```"
        )
        code = extract_python_code(content)
        assert "flask-cors" not in code
        assert "Flask" in code

    def test_fallback_raw_python(self) -> None:
        content = "from flask import Flask\napp = Flask(__name__)\n\ndef hello():\n    return 'hi'\n"
        code = extract_python_code(content)
        assert "Flask" in code

    def test_empty(self) -> None:
        assert extract_python_code("") == ""
        assert extract_python_code("no code here at all") == ""


class TestExtractFrontendCode:
    """Tests for extract_frontend_code()."""

    def test_jsx_block(self) -> None:
        content = "```jsx:App.jsx\nexport default function App() { return <div/> }\n```"
        code = extract_frontend_code(content)
        assert "App" in code

    def test_multiple_frontend_blocks(self) -> None:
        content = "```jsx:App.jsx\nfunction App() {}\n```\n\n```css:styles.css\n.app { color: red; }\n```"
        code = extract_frontend_code(content)
        assert "App" in code
        assert "color: red" in code

    def test_fallback_raw(self) -> None:
        content = "export default function App() { return <div>Hello</div> }"
        code = extract_frontend_code(content)
        assert "App" in code


class TestInferPythonDependencies:
    """Tests for infer_python_dependencies()."""

    def test_flask_deps(self) -> None:
        code = (
            "from flask import Flask\n"
            "from flask_sqlalchemy import SQLAlchemy\n"
            "from flask_cors import CORS\n"
            "import jwt\n"
        )
        deps = infer_python_dependencies(code)
        assert "flask" in deps
        assert "flask-sqlalchemy" in deps
        assert "flask-cors" in deps
        assert "pyjwt" in deps

    def test_excludes_stdlib(self) -> None:
        code = "import os\nimport json\nimport datetime\nfrom collections import defaultdict\n"
        deps = infer_python_dependencies(code)
        assert len(deps) == 0

    def test_excludes_local(self) -> None:
        code = "from app.models import User\nfrom config import settings\n"
        deps = infer_python_dependencies(code)
        assert len(deps) == 0

    def test_syntax_error_fallback(self) -> None:
        code = "from flask import Flask\nthis is not valid python {{{{"
        deps = infer_python_dependencies(code)
        assert "flask" in deps


class TestParseResultToStructured:
    """Tests for parse_result_to_structured()."""

    def test_backend_only(self) -> None:
        backend = "```python:app.py\nfrom flask import Flask\napp = Flask(__name__)\n```"
        result = parse_result_to_structured(backend)
        assert "backend_code" in result
        assert "Flask" in result["backend_code"]
        assert "backend_dependencies" in result

    def test_with_frontend(self) -> None:
        backend = "```python:app.py\nfrom flask import Flask\n```"
        frontend = "```jsx:App.jsx\nfunction App() {}\n```"
        result = parse_result_to_structured(backend, frontend)
        assert "backend_code" in result
        assert "frontend_code" in result
        assert "App" in result["frontend_code"]
