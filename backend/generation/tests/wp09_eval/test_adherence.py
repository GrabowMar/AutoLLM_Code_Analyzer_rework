"""WP-09: the adherence checker on fixture trees (spec 8.7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.generation.contracts.evaluation import AdherenceRecord
from backend.generation.evaluation.tester import checker_module

FLASK = "from flask import Flask\napp = Flask(__name__)\n"
FASTAPI = "from fastapi import FastAPI\napp = FastAPI()\n"


def stack(backend: str, frontend: str = "react", database: str = "sqlite", choice: str = "fixed") -> dict[str, Any]:
    return {
        "choice_mode": choice,
        "backend": {"language": "python", "framework": backend, "runtime": "python3.13"},
        "frontend": {"framework": frontend, "language": "ts", "build_tool": "vite"},
        "database": database,
    }


def tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


def analyse(root: Path, expect: dict[str, Any]) -> dict[str, Any]:
    record = checker_module().analyse(root, expect)
    AdherenceRecord.model_validate(record)  # the contract holds
    return record


def react_frontend(app_tsx: str = "export default function App() { return <div/> }\n") -> dict[str, str]:
    return {
        "frontend/package.json": json.dumps({"dependencies": {"react": "19.0.0", "react-dom": "19.0.0"}}),
        "frontend/src/App.tsx": "import React from 'react'\n" + app_tsx,
    }


def test_flask_labelled_fastapi_is_violated(tmp_path: Path) -> None:
    root = tree(tmp_path, {"backend/app.py": FLASK, "backend/requirements.txt": "flask==3.1.0\n", **react_frontend()})
    record = analyse(root, {"stack": stack("fastapi"), "auth_mode": "contract_bearer"})
    assert record["detected"]["backend"]["value"] == "flask"
    assert record["detected"]["backend"]["confidence"] == "high"
    assert record["status"] == "violated"
    assert [d["dimension"] for d in record["deviations"]] == ["backend"]


def test_matching_stack_is_compliant_and_lists_packages(tmp_path: Path) -> None:
    root = tree(
        tmp_path,
        {"backend/app.py": FASTAPI, "backend/requirements.txt": "fastapi\nuvicorn==0.30.0\n", **react_frontend()},
    )
    record = analyse(root, {"stack": stack("fastapi"), "auth_mode": "contract_bearer", "runtime": {"auth": "bearer"}})
    assert record["status"] == "compliant"
    assert record["unpinned"] == ["fastapi"]
    assert {p["name"] for p in record["packages"]} >= {"fastapi", "uvicorn", "react"}
    assert record["detected"]["auth"]["value"] == "bearer"


def test_tailwind_class_share(tmp_path: Path) -> None:
    app = 'export default () => <div className="flex p-4 text-sm bg-white rounded-lg custom-card"/>\n'
    files = react_frontend(app)
    files["frontend/package.json"] = json.dumps(
        {"dependencies": {"react": "19.0.0"}, "devDependencies": {"tailwindcss": "4.0.0"}},
    )
    root = tree(tmp_path, {"backend/app.py": FLASK, **files})
    record = analyse(root, {"stack": stack("flask"), "style": {"system": "tailwind"}, "auth_mode": "contract_bearer"})
    assert record["style_metrics"]["tailwind_class_share"] == 0.8333
    assert record["detected"]["styling"]["value"] == "tailwind"
    assert not [d for d in record["deviations"] if d["dimension"] == "styling"]


def test_component_library_imports_against_plain_css_are_partial(tmp_path: Path) -> None:
    app = (
        "import Button from '@mui/material/Button'\nimport { TextField } from '@mui/material'\n"
        "export default () => <Button/>\n"
    )
    root = tree(tmp_path, {"backend/app.py": FLASK, **react_frontend(app)})
    record = analyse(root, {"stack": stack("flask"), "style": {"system": "plain-css"}, "auth_mode": "contract_bearer"})
    assert record["style_metrics"]["component_library_imports"] == {"@mui/material": 2}
    assert record["detected"]["styling"]["value"] == "component-library"
    assert record["status"] == "partial"


def test_auth_mode_mismatch_is_violated(tmp_path: Path) -> None:
    root = tree(tmp_path, {"backend/app.py": FLASK, **react_frontend()})
    record = analyse(root, {"stack": stack("flask"), "auth_mode": "cookie_session", "runtime": {"auth": "bearer"}})
    assert record["status"] == "violated"
    assert [d["dimension"] for d in record["deviations"]] == ["auth"]


def test_free_choice_records_the_pick_and_is_compliant(tmp_path: Path) -> None:
    root = tree(tmp_path, {"server/main.py": FASTAPI, "server/db.py": "import sqlite3\n", **react_frontend()})
    record = analyse(root, {"stack": stack("free", "free", "free", choice="free"), "auth_mode": "contract_bearer"})
    assert record["status"] == "compliant"
    assert record["detected"]["backend"]["value"] == "fastapi"
    assert record["detected"]["database"]["value"] == "sqlite"
    assert record["detected"]["frontend"]["value"] == "react"


# --- Go (go.mod) and Java (pom.xml / Gradle): phase-B stacks ---------------------------------------------

GO_MOD = """module app

go 1.25.0

require modernc.org/sqlite v1.59.0

require (
\tgithub.com/google/uuid v1.6.0 // indirect
\tmodernc.org/libc v1.75.7 // indirect
)
"""
GO_MAIN = """package main

import (
\t"database/sql"
\t"log"
\t"net/http"

\t_ "modernc.org/sqlite"
)

func main() {
\tdb, _ := sql.Open("sqlite", "./data/app.db")
\t_ = db
\tlog.Fatal(http.ListenAndServe(":8000", http.NewServeMux()))
}
"""
POM = """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>4.1.2</version>
  </parent>
  <properties><java.version>21</java.version><jjwt.version>0.12.6</jjwt.version></properties>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-webmvc</artifactId>
    </dependency>
    <dependency><groupId>org.xerial</groupId><artifactId>sqlite-jdbc</artifactId><scope>runtime</scope></dependency>
    <dependency><groupId>io.jsonwebtoken</groupId><artifactId>jjwt-api</artifactId><version>${jjwt.version}</version></dependency>
    <dependency><groupId>com.example</groupId><artifactId>snap</artifactId><version>1.0-SNAPSHOT</version></dependency>
    <!-- <dependency><groupId>commented</groupId><artifactId>out</artifactId></dependency> -->
  </dependencies>
  <build><plugins><plugin>
    <groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId>
    <dependencies><dependency><groupId>plugin</groupId><artifactId>only</artifactId></dependency></dependencies>
  </plugin></plugins></build>
</project>
"""
JAVA_APP = """package app;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class Application {
    public static void main(String[] args) { SpringApplication.run(Application.class, args); }
}
"""


def go_stack(framework: str = "go-stdlib", database: str = "sqlite") -> dict[str, Any]:
    return {
        "choice_mode": "fixed",
        "backend": {"language": "go", "framework": framework, "runtime": "go1.25"},
        "frontend": {"framework": "none", "language": "none", "build_tool": "none"},
        "database": database,
    }


def java_stack(database: str = "sqlite") -> dict[str, Any]:
    return {
        "choice_mode": "fixed",
        "backend": {"language": "java", "framework": "spring-boot", "runtime": "temurin21"},
        "frontend": {"framework": "none", "language": "none", "build_tool": "none"},
        "database": database,
    }


def test_go_stdlib_with_sqlite_is_compliant_and_parses_go_mod(tmp_path: Path) -> None:
    root = tree(tmp_path, {"backend/go.mod": GO_MOD, "backend/main.go": GO_MAIN})
    record = analyse(root, {"stack": go_stack(), "auth_mode": "contract_bearer"})
    assert record["status"] == "compliant"
    backend = record["detected"]["backend"]
    assert (backend["value"], backend["confidence"]) == ("go-stdlib", "high")
    assert "manifest:backend/go.mod:go 1.25.0" in backend["evidence"]
    database = record["detected"]["database"]
    assert (database["value"], database["confidence"]) == ("sqlite", "high")
    assert "conn:backend/main.go:sql.Open(sqlite)" in database["evidence"]
    assert [(p["ecosystem"], p["name"], p["version"], p["pinned"]) for p in record["packages"]] == [
        ("go", "modernc.org/sqlite", "v1.59.0", True),
        ("go", "github.com/google/uuid", "v1.6.0", True),
        ("go", "modernc.org/libc", "v1.75.7", True),
    ]
    assert record["unpinned"] == []


def test_go_router_module_against_go_stdlib_is_violated(tmp_path: Path) -> None:
    main = GO_MAIN.replace('"net/http"', '"net/http"\n\n\t"github.com/gin-gonic/gin"')
    go_mod = GO_MOD.replace(
        "require modernc.org/sqlite v1.59.0",
        "require (\n\tgithub.com/gin-gonic/gin v1.10.0\n\tmodernc.org/sqlite v1.59.0\n)",
    )
    root = tree(tmp_path, {"backend/go.mod": go_mod, "backend/main.go": main})
    record = analyse(root, {"stack": go_stack(), "auth_mode": "contract_bearer"})
    assert record["detected"]["backend"]["value"] == "gin"
    assert record["status"] == "violated"
    assert [(d["dimension"], d["expected"], d["observed"]) for d in record["deviations"]] == [
        ("backend", "go-stdlib", "gin"),
    ]


def test_go_postgres_driver_against_sqlite_is_violated(tmp_path: Path) -> None:
    main = GO_MAIN.replace('_ "modernc.org/sqlite"', '_ "github.com/jackc/pgx/v5/stdlib"').replace(
        'sql.Open("sqlite"',
        'sql.Open("pgx"',
    )
    go_mod = GO_MOD.replace("modernc.org/sqlite v1.59.0", "github.com/jackc/pgx/v5 v5.7.2")
    root = tree(tmp_path, {"backend/go.mod": go_mod, "backend/main.go": main})
    record = analyse(root, {"stack": go_stack(), "auth_mode": "contract_bearer"})
    assert record["detected"]["database"]["value"] == "postgres"
    assert [d["dimension"] for d in record["deviations"]] == ["database"]


def test_spring_boot_pom_is_compliant_and_parses_dependencies(tmp_path: Path) -> None:
    root = tree(
        tmp_path,
        {
            "backend/pom.xml": POM,
            "backend/src/main/java/app/Application.java": JAVA_APP,
            "backend/src/main/resources/application.properties": "spring.datasource.url=jdbc:sqlite:./data/app.db\n",
        },
    )
    record = analyse(root, {"stack": java_stack(), "auth_mode": "contract_bearer"})
    assert record["status"] == "compliant"
    assert (record["detected"]["backend"]["value"], record["detected"]["backend"]["confidence"]) == (
        "spring-boot",
        "high",
    )
    assert (record["detected"]["database"]["value"], record["detected"]["database"]["confidence"]) == (
        "sqlite",
        "high",
    )
    assert [(p["ecosystem"], p["name"], p["version"], p["pinned"]) for p in record["packages"]] == [
        ("maven", "org.springframework.boot:spring-boot-starter-webmvc", None, True),  # managed by the parent
        ("maven", "org.xerial:sqlite-jdbc", None, True),
        ("maven", "io.jsonwebtoken:jjwt-api", "0.12.6", True),  # ${jjwt.version} resolved
        ("maven", "com.example:snap", "1.0-SNAPSHOT", False),
    ]
    assert record["unpinned"] == ["com.example:snap"]


def test_pom_without_a_parent_leaves_versionless_dependencies_unpinned(tmp_path: Path) -> None:
    pom = POM.split("<parent>", maxsplit=1)[0] + POM.split("</parent>")[1]
    root = tree(tmp_path, {"backend/pom.xml": pom, "backend/src/main/java/app/Application.java": JAVA_APP})
    record = analyse(root, {"stack": java_stack(), "auth_mode": "contract_bearer"})
    pinned = {p["name"]: p["pinned"] for p in record["packages"]}
    assert pinned["org.springframework.boot:spring-boot-starter-webmvc"] is False
    assert pinned["io.jsonwebtoken:jjwt-api"] is True


def test_spring_boot_with_postgres_driver_against_sqlite_is_violated(tmp_path: Path) -> None:
    pom = POM.replace(
        "<groupId>org.xerial</groupId><artifactId>sqlite-jdbc</artifactId>",
        ("<groupId>org.postgresql</groupId><artifactId>postgresql</artifactId>"),
    )
    root = tree(tmp_path, {"backend/pom.xml": pom, "backend/src/main/java/app/Application.java": JAVA_APP})
    record = analyse(root, {"stack": java_stack(), "auth_mode": "contract_bearer"})
    assert record["detected"]["database"]["value"] == "postgres"
    assert record["status"] == "violated"


def test_gradle_build_is_parsed_like_maven(tmp_path: Path) -> None:
    gradle = (
        "plugins { id 'org.springframework.boot' version '4.1.2' }\n"
        "dependencies {\n"
        "    implementation 'org.springframework.boot:spring-boot-starter-web'\n"
        '    runtimeOnly("org.xerial:sqlite-jdbc:3.47.1.0")\n'
        "    implementation 'com.example:dyn:1.+'\n"
        "}\n"
    )
    root = tree(tmp_path, {"build.gradle": gradle, "src/main/java/app/Application.java": JAVA_APP})
    record = analyse(root, {"stack": java_stack(), "auth_mode": "contract_bearer"})
    assert record["detected"]["backend"]["value"] == "spring-boot"
    assert record["detected"]["database"]["value"] == "sqlite"
    assert {p["name"]: p["pinned"] for p in record["packages"]} == {
        "org.springframework.boot:spring-boot-starter-web": True,
        "org.xerial:sqlite-jdbc": True,
        "com.example:dyn": False,
    }
