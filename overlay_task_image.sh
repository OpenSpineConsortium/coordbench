#!/bin/bash
# Make a pulled CooperBench task image usable in this cloud session, under
# its own name so the runner uses it unchanged: trust the egress proxy's CA
# (pip in runner.sh, claude to the API, git and npm all go through it), add
# Claude Code (the image's own installer needs apt and nodesource, which
# this environment's network policy blocks), and add the redis client the
# coop and team tools need (its pip install in the container fails quietly
# otherwise and messaging breaks). A glibc image gets the session's own
# claude binary; an Alpine (musl) image, where that binary cannot run, gets
# Node from the official Alpine image and the same Claude Code version from
# npm, whose registry the policy allows.
set -euo pipefail
image=$1
ctx=$(mktemp -d)
cp /root/.ccr/ca-bundle.crt "$ctx"/proxy-ca.crt
version=$(claude --version | awk '{print $1}')
trust='COPY proxy-ca.crt /etc/ssl/certs/proxy-ca-bundle.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/proxy-ca-bundle.crt \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/proxy-ca-bundle.crt \
    PIP_CERT=/etc/ssl/certs/proxy-ca-bundle.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/proxy-ca-bundle.crt \
    GIT_SSL_CAINFO=/etc/ssl/certs/proxy-ca-bundle.crt \
    CARGO_HTTP_CAINFO=/etc/ssl/certs/proxy-ca-bundle.crt \
    UV_NATIVE_TLS=1'
redis='RUN pip install -q redis || pip3 install -q redis || python3 -m pip install -q --break-system-packages redis'
if docker run --rm --entrypoint sh "$image" -c 'test -e /lib/ld-musl-x86_64.so.1'; then
    cat > "$ctx"/Dockerfile <<DOCKER
FROM mirror.gcr.io/library/node:22-alpine AS node
FROM $image
$trust
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node /usr/lib/libstdc++.so.6 /usr/lib/libgcc_s.so.1 /usr/lib/
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && npm install -g --silent @anthropic-ai/claude-code@$version && claude --version
$redis
LABEL cooperbench.overlay=cloud-session
DOCKER
else
    cp "$(readlink -f "$(command -v claude)")" "$ctx"/claude
    cat > "$ctx"/Dockerfile <<DOCKER
FROM $image
$trust
COPY claude /usr/local/bin/claude
RUN claude --version
$redis
LABEL cooperbench.overlay=cloud-session
DOCKER
fi
docker build -q -t "$image" "$ctx"
rm -rf "$ctx"
