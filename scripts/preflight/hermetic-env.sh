# Sourced by every gate. A gate's writes stay inside the repository, and its
# behaviour does not depend on ambient state.
#
# Stated once as a property of every gate rather than patched into the one that
# failed, because patching the offender leaves the next gate free to repeat it.
# The offender was the web leg: it inherited npm and corepack cache locations
# under the user's home, and inside a sandboxed executor those are unwritable,
# so the gate died with an internal package-manager error that said nothing
# about this repository. That aborted a parcel's baseline and cost a dispatch
# round.
#
# Measured, not assumed: before this file existed, one web gate run created
# .npm/_logs, .npm/_update-notifier-last-checked and .config/nextjs-nodejs in
# the caller's home. Next.js writes its config store even with telemetry
# disabled, which is why XDG_CONFIG_HOME is redirected too and not only the
# telemetry flag set.
#
# Callers must have `repo_root` set.

: "${repo_root:?hermetic-env.sh requires repo_root}"

_preflight_cache="$repo_root/web/.preflight-cache"

# Python: never write bytecode. A dirty tree blocks the next parcel dispatch.
export PYTHONDONTWRITEBYTECODE=1

# Node toolchain: caches, tool state and config store, all inside the tree.
export npm_config_cache="$_preflight_cache/npm"
export COREPACK_HOME="$_preflight_cache/corepack"
export XDG_CONFIG_HOME="$_preflight_cache/config"
export NEXT_TELEMETRY_DISABLED=1

unset _preflight_cache
