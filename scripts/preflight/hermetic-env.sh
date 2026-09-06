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
# Placement follows the spec: npm and corepack are the web component's tool
# state and live under web/. XDG_CONFIG_HOME is not any one component's -- it is
# exported for all six gates -- so it lives at the repository root.
#
# Note one deliberate side effect: git's default excludes file is
# $XDG_CONFIG_HOME/git/ignore, so redirecting it also stops a developer's global
# ignore list reaching the gates. That is wanted -- the file set should be the
# same for everyone -- and repo_files.py additionally clears core.excludesFile
# so the property holds even when a gate is run without this file.
#
# Callers must have `repo_root` set, and must treat a failure to source this
# file as a gate failure: these scripts run without `set -e`, so an unsourced
# environment would otherwise degrade silently to the ambient one.

: "${repo_root:?hermetic-env.sh requires repo_root}"

# Python: never write bytecode. Note that __pycache__ is gitignored, so a
# regression here is invisible to a working-tree check -- it is enforced by
# setting the variable, not by a test that could observe the difference.
export PYTHONDONTWRITEBYTECODE=1

# Node toolchain: caches and tool state, inside the component being checked.
export npm_config_cache="$repo_root/web/.preflight-cache/npm"
export COREPACK_HOME="$repo_root/web/.preflight-cache/corepack"
export NEXT_TELEMETRY_DISABLED=1

# Shared across every gate, so not under any one component.
export XDG_CONFIG_HOME="$repo_root/.preflight-cache/config"
