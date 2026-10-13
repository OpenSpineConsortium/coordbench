#!/bin/bash
# Build one CooperBench task image here from the task's own Dockerfile.
# In a Claude Code cloud session, whose outbound traffic goes through an
# egress proxy with a CA of its own (/root/.ccr/ca-bundle.crt), the
# Dockerfile is rewritten first: the proxy's CA is trusted so git, pip and
# uv can reach the network, the base image is taken from mirror.gcr.io
# since Docker Hub rate-limits that host, and the apt step is dropped
# since deb.debian.org is outside the session's egress policy (the full
# image already holds what it installs). The proxy serves HTTPS only (a
# plain-HTTP request gets 405), so no HTTP_PROXY is passed. On a host with
# no such CA file (a GitHub-hosted runner, a machine of the owner's) the
# Dockerfile builds as the dataset wrote it.
# Usage: build_task_image.sh <task directory> <image tag>
set -euo pipefail
task_dir=$1
tag=$2
ctx=$(mktemp -d)
cp -r "$task_dir"/. "$ctx"/
ca=${COORDBENCH_PROXY_CA:-/root/.ccr/ca-bundle.crt}
if [ -f "$ca" ]; then
    cp "$ca" "$ctx"/proxy-ca.crt
    python3 - "$ctx/Dockerfile" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path).read()
trust = ("\nCOPY proxy-ca.crt /etc/ssl/certs/proxy-ca-bundle.crt\n"
         "ENV SSL_CERT_FILE=/etc/ssl/certs/proxy-ca-bundle.crt "
         "GIT_SSL_CAINFO=/etc/ssl/certs/proxy-ca-bundle.crt "
         "PIP_CERT=/etc/ssl/certs/proxy-ca-bundle.crt "
         "REQUESTS_CA_BUNDLE=/etc/ssl/certs/proxy-ca-bundle.crt "
         "NODE_EXTRA_CA_CERTS=/etc/ssl/certs/proxy-ca-bundle.crt "
         "UV_NATIVE_TLS=1\n"
         )
# deb.debian.org is outside this environment's egress policy, so apt cannot
# run; the full (non-slim) image already holds what the apt step installs
# (git, curl, wget, gcc, make), and the apt step is dropped.
APT_STEP = re.compile(r"^RUN apt-get update(?:[^\n]*\\\n)*[^\n]*", re.M)
text = APT_STEP.sub("RUN git --version && gcc --version | head -n 1", text)
def mirror(match):
    image = match.group(1)
    if "/" not in image.split(":")[0]:
        image = "mirror.gcr.io/library/" + image
    image = image.replace("-slim", "")
    return f"FROM {image}{match.group(2)}{trust}"
text = re.sub(r"^FROM\s+(\S+)(.*)$", mirror, text, count=1, flags=re.M)
open(path, "w").write(text)
PY
    docker build --network host \
      --build-arg HTTPS_PROXY="${HTTPS_PROXY:-}" --build-arg https_proxy="${HTTPS_PROXY:-}" \
      --build-arg NO_PROXY="${NO_PROXY:-}" \
      -t "$tag" "$ctx"
else
    docker build -t "$tag" "$ctx"
fi
rm -rf "$ctx"
