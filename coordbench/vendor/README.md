# coordbench/vendor

sdlc_protocol here is a copy of the claim protocol's library,
OpenSpineConsortium/sdlc-protocol, which the sdlc arm
(coordbench/arms/sdlc/arm.py) decides through. VENDORED beside it
records the copy: its first line names the version and the commit the
copy was made from, and each line after it the sha256 of one file and
the file's path inside the copy.

Never edit the copy by hand. scripts/check.sh, the check CI runs on
every pull request, starts with

    python3 coordbench/vendor/sdlc_protocol/vendoring.py verify coordbench/vendor

which fails on a file that differs from VENDORED, a file missing or a
file added. VENDORED sits in the same tree, so a pull request that edits
the copy could rewrite it to match; CI therefore clones sdlc-protocol,
which is public, and names the clone in SDLC_PROTOCOL, and
tests/test_coordbench_vendor.py compares every file of the copy with the
library's at the commit VENDORED names, failing when they differ or when
the clone has no such commit. A change to the protocol is made in sdlc-protocol, by pull
request there; once it merges, that repository's sync workflow vendors
it into this one and opens the pull request sdlc-protocol/sync here, or
updates the one already open, and this repository's own check decides
it.

To vendor by hand, from a clone of sdlc-protocol at the commit wanted:

    python3 scripts/vendor.py <this repository>

It replaces sdlc_protocol here and rewrites VENDORED; land the result by
pull request. sdlc-protocol's ECOSYSTEM.md,
https://github.com/OpenSpineConsortium/sdlc-protocol/blob/main/ECOSYSTEM.md,
says how a change travels through every repository that takes a copy.
