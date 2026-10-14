#!/bin/bash
# Make room on a GitHub-hosted runner before a task image is pulled: the
# image's compressed size is one to four gigabytes and about twice that
# unpacked, and the runner's 14 GB free is partly taken by toolchains
# this job never uses. Each removal is best effort; the free space is
# printed before and after so a job's log says what it had.
set -u
df -h / | tail -1
for path in /usr/share/dotnet /usr/local/lib/android /opt/ghc /usr/local/.ghcup /opt/hostedtoolcache/CodeQL \
            /usr/local/share/powershell /usr/share/swift /usr/local/share/chromium; do
    sudo rm -rf "$path" 2>/dev/null || true
done
sudo docker image prune -af >/dev/null 2>&1 || true
df -h / | tail -1
