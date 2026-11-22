#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

git submodule sync --recursive
git submodule update --init --recursive

sdk_patch="$repo_root/patches/software-agent-sdk-docker-startup.patch"
if git -C software-agent-sdk apply --reverse --check "$sdk_patch" 2>/dev/null; then
    printf '%s\n' 'SDK Docker startup patch is already applied.'
else
    git -C software-agent-sdk apply --check "$sdk_patch"
    git -C software-agent-sdk apply "$sdk_patch"
    printf '%s\n' 'Applied SDK Docker startup patch.'
fi

uv sync "$@"
