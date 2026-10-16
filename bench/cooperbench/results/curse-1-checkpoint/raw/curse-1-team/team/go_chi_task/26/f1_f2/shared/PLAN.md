# Plan (agent1, team-lead)

Feature 1 (agent1, me): http.Request.PathValue support for Go 1.22.
  - NEW file path_value.go (//go:build go1.22): const supportsPathValue = true; func setPathValue(rctx *Context, r *http.Request)
  - NEW file path_value_fallback.go (//go:build !go1.22): const supportsPathValue = false; no-op setPathValue
  - mux.go: ONE small edit inside (*Mux).routeHTTP, in the `if _, _, h := mx.tree.FindRoute(...); h != nil {` block, just before h.ServeHTTP(w, r).

Feature 2 (agent2): you own your own spec. Please keep your edits out of the FindRoute block in routeHTTP
  and do not create files named path_value*.go. If you must touch mux.go, tell me which function(s)/lines
  via coop-send so we keep disjoint regions.

Hand-off: when done, put your diff (git diff of only your source changes, no tests/scratch) at
/workspace/shared/agent2.patch and mark your task done with coop-task-update <id> done.
I will apply it on top of my tree, build, and write the final patch.txt.
