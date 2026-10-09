#!/bin/bash
# Make a pulled CooperBench task image usable in this cloud session, under
# its own name so the runner uses it unchanged: trust the egress proxy's CA
# (pip in runner.sh, claude to the API, git and npm all go through it), add
# the host's claude binary (the image's own installer needs apt and
# nodesource, which this environment's network policy blocks), and add the
# redis client the coop and team tools need (its pip install in the
# container fails quietly otherwise and messaging breaks).
set -euo pipefail
image=$1
ctx=$(mktemp -d)
cp /root/.ccr/ca-bundle.crt "$ctx"/proxy-ca.crt
cp "$(readlink -f "$(command -v claude)")" "$ctx"/claude
cat > "$ctx"/Dockerfile <<DOCKER
FROM $image
COPY proxy-ca.crt /etc/ssl/certs/proxy-ca-bundle.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/proxy-ca-bundle.crt \\
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/proxy-ca-bundle.crt \\
    PIP_CERT=/etc/ssl/certs/proxy-ca-bundle.crt \\
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/proxy-ca-bundle.crt \\
    GIT_SSL_CAINFO=/etc/ssl/certs/proxy-ca-bundle.crt \\
    CARGO_HTTP_CAINFO=/etc/ssl/certs/proxy-ca-bundle.crt \\
    UV_NATIVE_TLS=1
COPY claude /usr/local/bin/claude
RUN (pip install -q redis || pip3 install -q redis || python3 -m pip install -q redis) && claude --version
LABEL cooperbench.overlay=cloud-session
DOCKER
docker build -q -t "$image" "$ctx"
rm -rf "$ctx"
