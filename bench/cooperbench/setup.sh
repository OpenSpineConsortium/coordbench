#!/bin/bash
# Bring up everything CooperBench needs on this host, idempotently, with no
# Claude usage: the Docker daemon, CooperBench at the pinned commit with
# this kit's one patch, its Python environment, Redis for messages and the
# task list, the git server for the shared-remote arm, and the tools the
# host may lack: uv, which CooperBench's environment is made with, and
# Claude Code at one pinned version, which overlay_task_image.sh copies
# into every task image so every agent runs the same build. A host that
# already has claude keeps it (a Claude Code cloud session runs its own);
# a run that spends no usage names a stand-in in COORDBENCH_CLAUDE_BIN and
# installs nothing.
# Images come from mirror.gcr.io (COORDBENCH_REGISTRY), since Docker Hub
# rate-limits a cloud session's host; the git server is built from a
# mirror image that already has git, since the session's network policy
# blocks Debian's package mirror.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=${COOPERBENCH_DIR:-$HOME/cooperbench}
commit=63b9d44d9f39a02fccf5bf0052db48a917a011fd
claude_version=${CLAUDE_CODE_VERSION:-2.1.296}
registry=${COORDBENCH_REGISTRY:-mirror.gcr.io/library}
export PATH="$HOME/.local/bin:$PATH"

if ! docker info >/dev/null 2>&1; then
    (dockerd >"${TMPDIR:-/tmp}/dockerd.log" 2>&1 &)
    for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
fi
docker info >/dev/null

if ! command -v uv >/dev/null 2>&1; then
    if command -v pipx >/dev/null 2>&1; then
        pipx install -q uv
    else
        python3 -m pip install -q --user uv
    fi
fi
uv --version >/dev/null

if [ -z "${COORDBENCH_CLAUDE_BIN:-}" ] && ! command -v claude >/dev/null 2>&1; then
    # Claude Code's native installer: one standalone binary under
    # ~/.local/bin, at the version named, which is what the overlay copies.
    curl -fsSL https://claude.ai/install.sh | bash -s "$claude_version"
fi
"${COORDBENCH_CLAUDE_BIN:-claude}" --version >/dev/null

if [ ! -d "$work/.git" ]; then
    git clone -q https://github.com/cooperbench/CooperBench "$work"
fi
git -C "$work" checkout -q "$commit"
if git -C "$work" apply --check "$here/cooperbench-claude-code-setup.patch" 2>/dev/null; then
    git -C "$work" apply "$here/cooperbench-claude-code-setup.patch"
fi
(cd "$work" && uv sync -q --python 3.11)

if ! docker exec cooperbench-redis redis-cli ping >/dev/null 2>&1; then
    docker rm -f cooperbench-redis >/dev/null 2>&1 || true
    docker run -d --name cooperbench-redis -p 6379:6379 "$registry/redis:alpine" >/dev/null
fi

if ! docker image inspect cooperbench-git-server:local >/dev/null 2>&1; then
    ctx=$(mktemp -d)
    cat >"$ctx/Dockerfile" <<DOCKER
FROM $registry/buildpack-deps:bookworm-scm
RUN mkdir /git
ENTRYPOINT ["git", "daemon", "--reuseaddr", "--export-all", "--enable=receive-pack", "--base-path=/git", "--listen=0.0.0.0", "/git"]
DOCKER
    docker build -q -t cooperbench-git-server:local "$ctx" >/dev/null
    rm -rf "$ctx"
fi
echo "ready: CooperBench at ${commit:0:7} in $work"
