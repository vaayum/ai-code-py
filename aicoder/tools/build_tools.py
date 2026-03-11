from __future__ import annotations

import sys
import subprocess
import shutil
from pathlib import Path

from langchain_core.tools import tool


class BuildTools:
    """Build and test tools auto-detecting the project's build system."""

    BUILD_SYSTEMS = {
        "pom.xml": ("maven", "mvn"),
        "build.gradle": ("gradle", "./gradlew"),
        "build.gradle.kts": ("gradle", "./gradlew"),
        "package.json": ("npm", "npm"),
        "Cargo.toml": ("cargo", "cargo"),
        "go.mod": ("go", "go"),
        "pyproject.toml": ("python", "python"),
        "setup.py": ("python", "python"),
        "Makefile": ("make", "make"),
    }

    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()
        self._system, self._cmd = self._detect()

    def _detect(self) -> tuple[str, str]:
        for marker, (system, cmd) in self.BUILD_SYSTEMS.items():
            if (self.root / marker).exists():
                return system, cmd
        return "unknown", "make"

    def get_tools(self) -> list:
        root = self.root
        system = self._system
        cmd = self._cmd

        @tool
        def get_build_system() -> str:
            """Detect and return the build system for this project."""
            return f"Build system: {system} (command: {cmd})\nRoot: {root}"

        @tool
        def compile_project() -> str:
            """Compile / build the project. Returns BUILD SUCCESS or FAILURE with errors."""
            build_cmd = {
                "maven": ["mvn", "compile", "-q"],
                "gradle": [cmd, "compileJava", "--quiet"],
                "npm": ["npm", "run", "build"],
                "cargo": ["cargo", "build"],
                "go": ["go", "build", "./..."],
                "python": [sys.executable, "-m", "py_compile"],
                "make": ["make"],
            }.get(system, ["make"])

            result = _run(build_cmd, root)
            if result.returncode == 0:
                return "✅ BUILD SUCCESS"
            return f"❌ BUILD FAILURE\n{result.stdout}\n{result.stderr}"

        @tool
        def run_tests() -> str:
            """Run the test suite. Returns pass/fail counts."""
            test_cmd = {
                "maven": ["mvn", "test"],
                "gradle": [cmd, "test"],
                "npm": ["npm", "test", "--", "--ci"],
                "cargo": ["cargo", "test"],
                "go": ["go", "test", "./..."],
                "python": [sys.executable, "-m", "pytest", "-v", "--tb=short"],
                "make": ["make", "test"],
            }.get(system, ["make", "test"])

            result = _run(test_cmd, root)
            output = (result.stdout + result.stderr).strip()
            status = "✅ TESTS PASSED" if result.returncode == 0 else "❌ TESTS FAILED"
            return f"{status}\n\n{output[-3000:]}"  # tail 3000 chars

        @tool
        def execute_shell(command: str) -> str:
            """Run an arbitrary shell command in the project root. Use with caution."""
            result = _run(command, root, shell=True)
            output = (result.stdout + result.stderr).strip()
            return output[-2000:] if output else "(no output)"

        return [get_build_system, compile_project, run_tests, execute_shell]


def _run(
    cmd: list[str] | str,
    cwd: Path,
    shell: bool = False,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        shell=shell,
        timeout=timeout,
    )
