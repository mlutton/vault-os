"""The engine registry: engine key -> adapter instance. vaultos.runner.core
routes a claimed job by its skill's `engine` field through this dict; a key
with no entry here fails the job fast to `error` (unknown/unconfigured
engine -- spec story 11). `script` (#22), `claude-cli` (#23), and
`cursor-cli` (#24) all ship here -- the spec's full v1 engine set."""

from .base import Engine, EngineContext, EngineResult
from .claude_cli import ClaudeCliEngine, ClaudeCliEngineError
from .cursor_cli import CursorCliEngine, CursorCliEngineError
from .script import ScriptEngine, ScriptEngineError

ENGINE_REGISTRY: dict[str, Engine] = {
    "script": ScriptEngine(),
    "claude-cli": ClaudeCliEngine(),
    "cursor-cli": CursorCliEngine(),
}

__all__ = [
    "ENGINE_REGISTRY",
    "ClaudeCliEngine",
    "ClaudeCliEngineError",
    "CursorCliEngine",
    "CursorCliEngineError",
    "Engine",
    "EngineContext",
    "EngineResult",
    "ScriptEngine",
    "ScriptEngineError",
]
