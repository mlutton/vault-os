"""The engine registry: engine key -> adapter instance. vaultos.runner.core
routes a claimed job by its skill's `engine` field through this dict; a key
with no entry here fails the job fast to `error` (unknown/unconfigured
engine -- spec story 11). `claude-cli` and `cursor-cli` are the spec's
named v2 adapters; this ticket ships `script` only."""

from .base import Engine, EngineContext, EngineResult
from .script import ScriptEngine, ScriptEngineError

ENGINE_REGISTRY: dict[str, Engine] = {
    "script": ScriptEngine(),
}

__all__ = [
    "ENGINE_REGISTRY",
    "Engine",
    "EngineContext",
    "EngineResult",
    "ScriptEngine",
    "ScriptEngineError",
]
