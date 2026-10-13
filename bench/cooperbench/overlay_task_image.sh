#!/bin/bash
# Make a pulled CooperBench task image usable on this host, under its own
# name so the runner uses it unchanged: add Claude Code (the image's own
# installer needs apt and nodesource, which a cloud session's network
# policy blocks, and one pinned binary keeps every agent on one version),
# add the redis client the coop and team tools need (its pip install in
# the container fails quietly otherwise and messaging breaks), and, where
# this host's outbound traffic goes through an egress proxy with a CA of
# its own (a Claude Code cloud session: /root/.ccr/ca-bundle.crt), trust
# that CA, since pip in runner.sh, claude to the API, git and npm all go
# through it. A host with no such file (a GitHub-hosted runner, a
# machine of the owner's) gets no trust lines.
# A glibc image gets this host's own claude binary, or the one
# COORDBENCH_CLAUDE_BIN names (the fake agent, in a run that spends no
# usage); an Alpine (musl) image, where that binary cannot run, gets Node
# from the official Alpine image and the same Claude Code version from
# npm, whose registry the policy allows. The image is labelled with the
# version it carries, so prepare_task.sh overlays it again when the
# host's version changes.
# Usage: overlay_task_image.sh <image>
set -euo pipefail
image=$1
ctx=$(mktemp -d)
claude_bin=$(readlink -f "${COORDBENCH_CLAUDE_BIN:-$(command -v claude)}")
version=$("$claude_bin" --version | awk '{print $1}')
registry=${COORDBENCH_REGISTRY:-mirror.gcr.io/library}
ca=${COORDBENCH_PROXY_CA:-/root/.ccr/ca-bundle.crt}
trust=''
origin=host
if [ -f "$ca" ]; then
    cp "$ca" "$ctx"/proxy-ca.crt
    trust='COPY proxy-ca.crt /etc/ssl/certs/proxy-ca-bundle.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/proxy-ca-bundle.crt \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/proxy-ca-bundle.crt \
    PIP_CERT=/etc/ssl/certs/proxy-ca-bundle.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/proxy-ca-bundle.crt \
    GIT_SSL_CAINFO=/etc/ssl/certs/proxy-ca-bundle.crt \
    CARGO_HTTP_CAINFO=/etc/ssl/certs/proxy-ca-bundle.crt \
    UV_NATIVE_TLS=1'
    origin=cloud-session
fi
redis='RUN pip install -q redis || pip3 install -q redis || python3 -m pip install -q --break-system-packages redis'
labels="LABEL cooperbench.overlay=$origin cooperbench.claude=$version"
if docker run --rm --entrypoint sh "$image" -c 'test -e /lib/ld-musl-x86_64.so.1'; then
    cat > "$ctx"/Dockerfile <<DOCKER
FROM $registry/node:22-alpine AS node
FROM $image
$trust
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node /usr/lib/libstdc++.so.6 /usr/lib/libgcc_s.so.1 /usr/lib/
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && npm install -g --silent @anthropic-ai/claude-code@$version && claude --version
$redis
$labels
DOCKER
else
    cp "$claude_bin" "$ctx"/claude
    chmod 755 "$ctx"/claude
    cat > "$ctx"/Dockerfile <<DOCKER
FROM $image
$trust
COPY claude /usr/local/bin/claude
RUN claude --version
$redis
$labels
DOCKER
fi
docker build -q -t "$image" "$ctx"
rm -rf "$ctx"
# With the containerd image store the build cache holds a second copy of
# the task image's layers, several gigabytes for the large tasks; drop it.
docker builder prune -af >/dev/null 2>&1 || true
