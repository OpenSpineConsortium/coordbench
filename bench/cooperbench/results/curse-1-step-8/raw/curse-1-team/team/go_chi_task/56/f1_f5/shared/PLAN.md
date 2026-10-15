# Plan (agent1 = lead)

Two features, one repo. Both of us touch context.go, mux.go, tree.go, so keep hunks small and disjoint.

agent1 (feature 1, Allow header on 405) edits ONLY these regions:
- context.go: one new field "methodsAllowed []methodTyp" directly after the "methodNotAllowed bool" field at the END of the Context struct, and one line in Reset() right after "x.methodNotAllowed = false".
- tree.go: new reverseMethodMap + one line in RegisterMethod (just after "methodMap[method] = mt"); inside findRoute only the two "rctx.methodNotAllowed = true" spots (I add a call just before each).
- mux.go: MethodNotAllowedHandler() becomes variadic, routeHTTP 405 call at the very end passes rctx.methodsAllowed, and methodNotAllowedHandler() at the very bottom of the file.
- middleware/allowed_methods.go (new).

agent2 (feature 5, CORS): please put new Context fields BEFORE "methodNotAllowed bool" (not after), put new code in new files (cors.go) where you can, and do not touch the lines above. Do not reformat anything.

Integration: each of you writes "git diff > /workspace/shared/<agent>.patch" (base = original HEAD, tracked files only; new files need "git add -N" first). agent1 applies agent2 patch with git apply --3way and fixes by hand.
