"""Run student code in a separate Python process with basic restrictions.

!!! NOT A PRODUCTION-GRADE SANDBOX !!!
What it does:
  1. Static check (ast): only an allow-list of modules may be imported; dangerous
     built-ins (open, exec, eval, __import__ ...) and dunder attributes are rejected.
  2. Runs the code in a fresh `python -I` subprocess, inside a throw-away temp
     directory, with a nearly empty environment.
  3. Hard timeout (process is killed) and an output-size cap (process is killed).
  4. On Linux/macOS only: CPU-time, memory and file-size limits via `resource`.
What it does NOT do: see README / "Security limitations". In short, a determined
attacker can probably escape; this is only meant to stop accidents and casual abuse
by friends you trust. Never expose it to the public internet.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field

from app import config

BLOCKED_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "breakpoint",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "exit", "quit",
})
ALLOWED_DUNDER_NAMES = frozenset({"__name__"})
MAX_STDIN_CHARS = 10_000


@dataclass
class SandboxConfig:
    timeout_seconds: float = 5.0
    max_output_bytes: int = 20_000
    max_code_chars: int = 10_000
    memory_limit_mb: int = 256                      # Linux/macOS only
    allowed_modules: frozenset = field(default_factory=frozenset)
    blocked_names: frozenset = BLOCKED_NAMES

    @classmethod
    def from_env(cls) -> "SandboxConfig":
        return cls(
            timeout_seconds=config.SANDBOX_TIMEOUT_SECONDS,
            max_output_bytes=config.SANDBOX_MAX_OUTPUT_BYTES,
            allowed_modules=frozenset(config.SANDBOX_ALLOWED_MODULES),
        )


@dataclass
class Violation:
    kind: str            # syntax_error | forbidden_import | forbidden_call | too_long
    message: str
    error_name: str = ""
    line: int | None = None


@dataclass
class RunResult:
    status: str          # ok | runtime_error | timeout | output_limit
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    duration: float = 0.0


def check_code(code: str, cfg: SandboxConfig) -> Violation | None:
    """Static safety/syntax check. Returns None when the code may be run."""
    if len(code) > cfg.max_code_chars:
        return Violation("too_long", f"Your code is longer than {cfg.max_code_chars} characters.")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return Violation("syntax_error", exc.msg or "invalid syntax", type(exc).__name__, exc.lineno)

    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in cfg.allowed_modules:
                    return Violation("forbidden_import", f"The module '{root}' is not allowed here.", line=line)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in cfg.allowed_modules:
                return Violation("forbidden_import", f"The module '{root or '.'}' is not allowed here.", line=line)
        elif isinstance(node, ast.Name):
            if node.id in cfg.blocked_names:
                return Violation("forbidden_call", f"'{node.id}' is not allowed in this course.", line=line)
            if node.id.startswith("__") and node.id not in ALLOWED_DUNDER_NAMES:
                return Violation("forbidden_call", f"'{node.id}' is not allowed in this course.", line=line)
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                return Violation("forbidden_call", f"Attributes starting with '__' ('{node.attr}') are not allowed.", line=line)
    return None


def _limit_resources(cfg: SandboxConfig):  # pragma: no cover - POSIX only
    def apply():
        try:
            import resource
            cpu = int(cfg.timeout_seconds) + 1
            for res, limit in (
                (resource.RLIMIT_CPU, cpu),
                (resource.RLIMIT_AS, cfg.memory_limit_mb * 1024 * 1024),
                (resource.RLIMIT_FSIZE, 1024 * 1024),
            ):
                try:
                    resource.setrlimit(res, (limit, limit))
                except (ValueError, OSError):
                    pass
        except ImportError:
            pass
    return apply


def _clean_env() -> dict:
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if os.name == "nt":
        for key in ("SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP"):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


def _pump(stream, buf: bytearray, limit: int, proc, overflow: threading.Event) -> None:
    """Read a pipe into buf; kill the process if it produces more than `limit` bytes."""
    try:
        while True:
            chunk = stream.read1(4096)
            if not chunk:
                return
            room = limit - len(buf)
            if room > 0:
                buf.extend(chunk[:room])
            if len(chunk) > room:
                overflow.set()
                proc.kill()
                return
    except (OSError, ValueError):
        return


def run_code(code: str, stdin_text: str = "", cfg: SandboxConfig | None = None) -> RunResult:
    """Run code. Returns RunResult; static violations are checked by the caller via check_code()."""
    cfg = cfg or SandboxConfig.from_env()
    with tempfile.TemporaryDirectory(prefix="learn_sandbox_") as tmp:
        script = os.path.join(tmp, "student_code.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(code)

        kwargs: dict = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=tmp, env=_clean_env())
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["preexec_fn"] = _limit_resources(cfg)

        started = time.monotonic()
        proc = subprocess.Popen([sys.executable, "-I", "-B", script], **kwargs)
        out, err = bytearray(), bytearray()
        overflow = threading.Event()
        threads = [
            threading.Thread(target=_pump, args=(proc.stdout, out, cfg.max_output_bytes, proc, overflow), daemon=True),
            threading.Thread(target=_pump, args=(proc.stderr, err, cfg.max_output_bytes, proc, overflow), daemon=True),
        ]
        for t in threads:
            t.start()
        try:
            proc.stdin.write(stdin_text[:MAX_STDIN_CHARS].encode("utf-8"))
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

        timed_out = False
        try:
            proc.wait(timeout=cfg.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()
        for t in threads:
            t.join(timeout=2)
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass

        duration = time.monotonic() - started
        stdout = out.decode("utf-8", errors="replace").replace("\r\n", "\n")
        stderr = err.decode("utf-8", errors="replace").replace("\r\n", "\n")
        if overflow.is_set():
            status = "output_limit"
        elif timed_out:
            status = "timeout"
        elif proc.returncode != 0:
            status = "runtime_error"
        else:
            status = "ok"
        return RunResult(status, stdout, stderr, proc.returncode, duration)
