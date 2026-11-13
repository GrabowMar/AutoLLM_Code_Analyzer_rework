"""Symbol index: route extractors per framework, components, unparsed files (spec section 7.3)."""

from __future__ import annotations

import textwrap

from backend.generation.slot.index import build_from_files


def _files(**files: str) -> dict[str, bytes]:
    return {
        path.replace("__", "/").replace("_DOT_", "."): textwrap.dedent(text).encode() for path, text in files.items()
    }


def _routes(index) -> set[tuple[str, str, str]]:
    return {(e.method, e.path, e.handler) for e in index.endpoints}


def test_flask_blueprint_prefix_and_tuple_methods() -> None:
    files = {
        "app/__init__.py": b"",
        "app/main.py": textwrap.dedent(
            """
            from flask import Flask
            from app.items import items

            app = Flask(__name__)
            app.register_blueprint(items, url_prefix="/api/items")

            @app.route("/api/health")
            def health():
                return {"ok": True}
            """,
        ).encode(),
        "app/items.py": textwrap.dedent(
            """
            from flask import Blueprint

            items = Blueprint("items", __name__)

            @items.route("", methods=("GET", "POST"))
            def collection():
                return {}

            @items.get("/<int:item_id>")
            def detail(item_id):
                return {}
            """,
        ).encode(),
    }
    build = build_from_files(
        files,
        spec_endpoints=[
            {"id": "items.list", "method": "GET", "path": "/api/items"},
            {"id": "items.get", "method": "GET", "path": "/api/items/{id}"},
        ],
    )
    routes = _routes(build.index)
    assert ("GET", "/api/items", "app/items.py::collection") in routes
    assert ("POST", "/api/items", "app/items.py::collection") in routes
    assert ("GET", "/api/items/{item_id}", "app/items.py::detail") in routes
    assert ("GET", "/api/health", "app/main.py::health") in routes
    by_handler = {(e.method, e.handler): e for e in build.index.endpoints}
    assert by_handler[("GET", "app/items.py::detail")].spec_endpoint_id == "items.get"
    assert by_handler[("GET", "app/items.py::collection")].confidence == "exact"
    kinds = {s.id: s.kind for s in build.index.symbols}
    assert kinds["app/items.py::detail"] == "route_handler"


def test_flask_unregistered_blueprint_is_heuristic() -> None:
    files = {
        "bp.py": (
            b"from flask import Blueprint\n"
            b'bp = Blueprint("x", __name__, url_prefix="/x")\n'
            b"\n"
            b'@bp.get("/y")\n'
            b"def y():\n"
            b"    return 1\n"
        ),
    }
    (endpoint,) = build_from_files(files).index.endpoints
    assert (endpoint.path, endpoint.confidence) == ("/x/y", "heuristic")


def test_fastapi_router_prefix_and_include_router() -> None:
    files = {
        "main.py": textwrap.dedent(
            """
            from fastapi import FastAPI
            from routers import todos

            app = FastAPI()
            app.include_router(todos.router, prefix="/api")

            @app.get("/health")
            async def health():
                return {}
            """,
        ).encode(),
        "routers/__init__.py": b"",
        "routers/todos.py": textwrap.dedent(
            """
            from fastapi import APIRouter

            router = APIRouter(prefix="/todos")

            @router.get("/{todo_id}")
            async def read_todo(todo_id: int):
                return {}

            @router.post("")
            async def create_todo():
                return {}
            """,
        ).encode(),
    }
    routes = _routes(build_from_files(files).index)
    assert ("GET", "/api/todos/{todo_id}", "routers/todos.py::read_todo") in routes
    assert ("POST", "/api/todos", "routers/todos.py::create_todo") in routes
    assert ("GET", "/health", "main.py::health") in routes


def test_django_urls_include_and_drf() -> None:
    files = {
        "project/urls.py": (
            b'from django.urls import include, path\n\nurlpatterns = [path("api/", include("notes.urls"))]\n'
        ),
        "notes/__init__.py": b"",
        "notes/urls.py": textwrap.dedent(
            """
            from django.urls import path
            from rest_framework.routers import DefaultRouter
            from . import views

            router = DefaultRouter()
            router.register(r"tags", views.TagViewSet)

            urlpatterns = [
                path("notes/", views.note_list),
                path("notes/<int:pk>/", views.NoteDetail.as_view()),
                path("", include(router.urls)),
            ]
            """,
        ).encode(),
        "notes/views.py": textwrap.dedent(
            """
            from rest_framework.decorators import api_view
            from rest_framework.views import APIView
            from rest_framework import viewsets

            @api_view(["GET", "POST"])
            def note_list(request):
                return None

            class NoteDetail(APIView):
                def get(self, request, pk):
                    return None

                def delete(self, request, pk):
                    return None

            class TagViewSet(viewsets.ModelViewSet):
                def list(self, request):
                    return None
            """,
        ).encode(),
    }
    build = build_from_files(files)
    routes = _routes(build.index)
    assert ("GET", "/api/notes", "notes/views.py::note_list") in routes
    assert ("POST", "/api/notes", "notes/views.py::note_list") in routes
    assert ("GET", "/api/notes/{pk}", "notes/views.py::NoteDetail.get") in routes
    assert ("DELETE", "/api/notes/{pk}", "notes/views.py::NoteDetail.delete") in routes
    assert ("GET", "/api/tags", "notes/views.py::TagViewSet.list") in routes
    viewset = [e for e in build.index.endpoints if e.path.startswith("/api/tags")]
    assert viewset
    assert all(e.confidence == "heuristic" for e in viewset)


def test_express_router_prefix_and_inline_handlers() -> None:
    files = {
        "server.js": textwrap.dedent(
            """
            const express = require("express");
            const todos = require("./routes/todos");
            const app = express();
            app.use("/api/todos", todos);
            app.get("/api/health", (req, res) => res.json({ ok: true }));
            module.exports = app;
            """,
        ).encode(),
        "routes/todos.js": textwrap.dedent(
            """
            const express = require("express");
            const router = express.Router();

            function listTodos(req, res) {
              res.json([]);
            }

            router.get("/", listTodos);
            router.patch("/:id", async (req, res) => {
              res.json({});
            });
            module.exports = router;
            """,
        ).encode(),
        "client/api.js": b'import axios from "axios";\nexport const load = () => axios.get("/api/todos");\n',
    }
    build = build_from_files(files)
    routes = _routes(build.index)
    assert ("GET", "/api/todos", "routes/todos.js::listTodos") in routes
    assert ("PATCH", "/api/todos/{id}", "routes/todos.js::PATCH /api/todos/{id}") in routes
    assert ("GET", "/api/health", "server.js::GET /api/health") in routes
    assert not any(e.handler.startswith("client/") for e in build.index.endpoints)  # an axios call is no route
    inline = next(s for s in build.index.symbols if s.id == "routes/todos.js::PATCH /api/todos/{id}")
    assert inline.kind == "route_handler"


def test_go_patterns_and_gin_groups() -> None:
    files = {
        "go.mod": b"module example.com/app\n\ngo 1.22\n",
        "main.go": textwrap.dedent(
            """
            package main

            import "net/http"

            func getTodo(w http.ResponseWriter, r *http.Request) {}

            func main() {
                mux := http.NewServeMux()
                mux.HandleFunc("GET /api/todos/{id}", getTodo)
                mux.HandleFunc("/api/health", func(w http.ResponseWriter, r *http.Request) {})
                http.ListenAndServe(":8000", mux)
            }
            """,
        ).encode(),
        "gin/routes.go": textwrap.dedent(
            """
            package gin

            import "github.com/gin-gonic/gin"

            type Handler struct{}

            func (h *Handler) List(c *gin.Context) {}

            func Register(r *gin.Engine, h *Handler) {
                api := r.Group("/api/v2")
                api.GET("/items/:id", h.List)
            }
            """,
        ).encode(),
    }
    build = build_from_files(files)
    by_route = {(e.method, e.path): e for e in build.index.endpoints}
    assert by_route[("GET", "/api/todos/{id}")].handler == "main.go::getTodo"
    assert by_route[("GET", "/api/todos/{id}")].confidence == "pattern"
    assert by_route[("ANY", "/api/health")].handler == "main.go::ANY /api/health"
    assert by_route[("GET", "/api/v2/items/{id}")].handler == "gin/routes.go::Handler.List"


def test_spring_class_and_method_mappings() -> None:
    files = {
        "src/main/java/app/TodoController.java": textwrap.dedent(
            """
            package app;

            import org.springframework.web.bind.annotation.*;

            @RestController
            @RequestMapping("/api/todos")
            public class TodoController {
                @GetMapping
                public String list() { return "[]"; }

                @PatchMapping("/{id}")
                public String update(@PathVariable long id) { return "{}"; }

                @RequestMapping(value = "/{id}", method = RequestMethod.DELETE)
                public void delete(@PathVariable long id) {}
            }
            """,
        ).encode(),
    }
    routes = _routes(build_from_files(files).index)
    base = "src/main/java/app/TodoController.java::TodoController"
    assert ("GET", "/api/todos", f"{base}.list") in routes
    assert ("PATCH", "/api/todos/{id}", f"{base}.update") in routes
    assert ("DELETE", "/api/todos/{id}", f"{base}.delete") in routes


def test_components_react_vue_svelte_and_unparsed_files() -> None:
    files = {
        "src/TodoList.tsx": (
            b"export default function TodoList({ items }: { items: string[] }) {\n"
            b"  return <ul>{items.map((i) => <li>{i}</li>)}</ul>;\n"
            b"}\n"
        ),
        "src/widgets.jsx": (
            b"export const Badge = () => <span/>;\n"
            b"export function Card() { return <div/>; }\n"
            b"function helper() { return 1; }\n"
        ),
        "src/Note.vue": (
            b"<template><p>{{ text }}</p></template>\n"
            b'<script setup lang="ts">\n'
            b'const text: string = "hi";\n'
            b"function shout(): string { return text.toUpperCase(); }\n"
            b"</script>\n"
        ),
        "src/Counter.svelte": (
            b"<script>\n"
            b"  let count = 0;\n"
            b"  function inc() { count += 1; }\n"
            b"</script>\n"
            b"<button on:click={inc}>{count}</button>\n"
        ),
        "src/broken.py": b"def oops(:\n    pass\n",
        "README.md": b"# readme\n",
    }
    build = build_from_files(files)
    components = {(c.name, c.framework, c.whole_file) for c in build.index.components}
    assert ("TodoList", "react", True) in components
    assert ("Badge", "react", False) in components
    assert ("Card", "react", False) in components
    assert ("Note", "vue", True) in components
    assert ("Counter", "svelte", True) in components
    assert not any(c.name == "helper" for c in build.index.components)
    # SFC script blocks are indexed with file coordinates.
    shout = next(s for s in build.index.symbols if s.id == "src/Note.vue::shout")
    assert files["src/Note.vue"][shout.span.start_byte : shout.span.end_byte].startswith(b"function shout")
    assert shout.span.start_line == 4
    unparsed = {f.path for f in build.index.files if f.language and not f.parsed}
    assert unparsed == {"src/broken.py"}
    from backend.generation.slot.index import stats_of

    assert stats_of(build.index).unparsed == ["src/broken.py"]


def test_golden_parent_index_matches_parent_app_yaml() -> None:
    from backend.generation.testing.golden_library import load_golden_library

    library = load_golden_library()
    task = library.get("task", "crud_todo_list").content
    build = build_from_files(
        dict(library.parent.files),
        spec_endpoints=[{"id": e["id"], "method": e["method"], "path": e["path"]} for e in task["endpoints"]],
    )
    by_spec = {e.spec_endpoint_id: e.handler for e in build.index.endpoints if e.spec_endpoint_id}
    expected = {eid: info["handler"] for eid, info in library.parent.meta["endpoints"].items()}
    assert by_spec == expected
    components = {c.name: c.path for c in build.index.components}
    assert components == dict(library.parent.meta["components"])


def test_index_is_deterministic() -> None:
    from backend.generation.testing.golden_library import load_golden_library

    files = dict(load_golden_library().parent.files)
    first = build_from_files(files).index.model_dump(mode="json")
    second = build_from_files(dict(reversed(list(files.items())))).index.model_dump(mode="json")
    assert first == second


def test_symbol_spans_snap_to_line_start_and_hash_their_slice() -> None:
    from backend.generation import identity

    source = b"class A:\n    @staticmethod\n    def f(x):\n        return x\n"
    build = build_from_files({"a.py": source})
    method = next(s for s in build.index.symbols if s.id == "a.py::A.f")
    assert method.span.start_line == 2
    assert source[method.span.start_byte : method.span.end_byte].startswith(b"    @staticmethod")
    assert method.content_sha == identity.text_sha(source[method.span.start_byte : method.span.end_byte].decode())
    assert method.decorators == ["@staticmethod"]
    assert method.parent == "a.py::A"
