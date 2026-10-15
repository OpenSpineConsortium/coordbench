# PLAN (agent1, lead)  -- repo: go-chi/chi at 7f28096

Tasks: cc46e8ad19 = feature 1 (Allow header on 405; agent1, lead), dc188be735 = feature 5 (agent2).
agent2 has not yet told me which regions of context.go / mux.go / tree.go its feature changes, so this
plan names agent1 regions exactly and gives every other region of those files, and cors.go, to agent2.

## Task cc46e8ad19 (agent1) owns
- middleware/allowed_methods.go (new file, only agent1)
- context.go: ONLY (a) the new struct field `methodsAllowed []methodTyp` placed right after the
  `methodNotAllowed bool` field, (b) the one line `x.methodsAllowed = x.methodsAllowed[:0]` in Context.Reset()
  right after `x.methodNotAllowed = false`.
- tree.go: ONLY (a) new `var reverseMethodMap` right after `methodMap`, (b) one line in RegisterMethod
  (`reverseMethodMap[mt] = method`), (c) the two `rctx.methodNotAllowed = true` blocks inside node.findRoute
  (each gets one call `xn.addMethodsAllowed(rctx)` just before the flag), (d) a new method
  `(*node).addMethodsAllowed` placed directly after node.findRoute.
- mux.go: ONLY (a) the 405 tail of Mux.routeHTTP (the `rctx.methodNotAllowed` branch), (b) Mux.MethodNotAllowedHandler,
  (c) the function methodNotAllowedHandler at the bottom of the file. SHARED with nothing else unless agent2 asks.

## Task dc188be735 (agent2) owns
- cors.go (new, only agent2)
- every region of context.go, mux.go, tree.go NOT listed above. Add your own code in new fields/functions
  at places that do not touch the lines above (e.g. append new functions at the end of a file, new struct
  fields at the END of the Context struct, after methodsAllowed is added by me, i.e. just before the closing brace).
- If your feature must change a region listed under agent1, write it in /workspace/shared/REQUESTS.md first.

## Integration
agent2 drops its diff at /workspace/shared/agent2.patch (git diff of its working tree, source files only, no tests unless the task asks).
agent1 applies it with git apply on top of its own tree, fixes conflicts, builds with go build ./... and go vet, runs go test ./..., then writes patch.txt.

## Update (agent1, after agent2 declared its regions)
- agent2 owns: cors.go (new); tree.go ONLY a new var methodNames and func GetMethodName appended directly
  after func methodTypString. context.go and mux.go: no edit by agent2.
- agent1 keeps every region listed above (agent1 is finished with context.go, mux.go and tree.go already; my tree.go additions
  sit between node.findRoute and node.findEdge, so the two regions are disjoint).
- agent2 may edit tree.go now, in that one region only. Note agent1 added reverseMethodMap (name -> type reversed) in tree.go; you may use it.
- Please drop your diff at /workspace/shared/agent2.patch when done (git diff of source files only).
