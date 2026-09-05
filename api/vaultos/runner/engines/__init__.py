"""The engine registry: engine key -> adapter instance. vaultos.runner.core
routes a claimed job by its skill's `engine` field through this dict; a key
with no entry here fails the job fast to `error` (unknown/unconfigured
engine -- spec story 11). `cursor-cli` is the spec's remaining named-but-
unbuilt v2 adapter; `script` (#22) and `claude-cli` (#23) both ship here."""

from .base import Engine, EngineContext, EngineResult
from .claude_cli import ClaudeCliEngine, ClaudeCliEngineError
from .script import ScriptEngine, ScriptEngineError

ENGINE_REGISTRY: dict[str, Engine] = {
    "script": ScriptEngine(),
    "claude-cli": ClaudeCliEngine(),
}

__all__ = [
    "ENGINE_REGISTRY",
    "ClaudeCliEngine",
    "ClaudeCliEngineError",
    "Engine",
    "EngineContext",
    "EngineResult",
    "ScriptEngine",
    "ScriptEngineError",
]
