# PLAN (lead: agent1)

Tasks: fea1d84149 = feature 1 (Allow header on 405, agent1); 6206b15a5e = feature 5 (agent2).

## Files only one feature changes
- middleware/allowed_methods.go (new): agent1 alone.
- cors.go (new): agent2 alone.

## Files both features change, split by region
context.go
- agent1: struct Context, new field methodsAllowed (right after methodNotAllowed); Context.Reset(), one new line resetting it.
- agent2: every other region of context.go (new fields go after methodsAllowed, new Reset lines go after agent1 line).

tree.go
- agent1: new var reverseMethodMap (right after methodMap); one line in RegisterMethod; the two blocks in node.findRoute that set rctx.methodNotAllowed = true; a new helper func addMethodAllowed placed directly after node.findRoute.
- agent2: every other region of tree.go.

mux.go
- agent1: func (mx *Mux) MethodNotAllowedHandler; the 405 branch at the end of routeHTTP (if rctx.methodNotAllowed {...}); func methodNotAllowedHandler (end of file).
- agent2: every other region of mux.go.

Regions as declared by agent2 (message 2026-10-15 18:57): cors.go new; mux.go three new methods EnableCORS/EnableCORSWithOptions/WithCORS right after Mux.Middlewares(); tree.go methodNames map and GetMethodName() right after methodTypString(); context.go untouched by agent2.
agent1 also adds one line in node.FindRoute (tree.go): rctx.methodsAllowed = rctx.methodsAllowed[:0] below the routeParams resets. agent2 does not touch FindRoute or findRoute.

SHARED (both touch, lead joins at integration): none. agent2: if your feature must change one of agent1 regions, write it in REQUESTS.md first.

Integration: lead applies /workspace/shared/agent2.patch onto the lead tree, fixes conflicts, builds, runs go test ./..., then writes patch.txt.
