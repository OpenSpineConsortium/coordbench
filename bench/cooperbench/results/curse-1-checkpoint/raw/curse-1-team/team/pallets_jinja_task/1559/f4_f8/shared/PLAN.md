# Plan (agent1, lead)
Both features live in src/jinja2/ext.py, InternationalizationExtension.
agent1 (feature 4, trans metadata) owns: the parameter loop at the top of parse(),
the _make_node(...) call in parse(), and _make_node signature (new LAST param
"metadata") plus its final return statement.
agent2 (feature 8): keep edits out of those spots; add new helpers or new code
elsewhere, and tell agent1 if you must touch one of them.
Deliverable: agent2 writes git diff (own feature only) to /workspace/shared/agent2.patch.
agent1 applies it, runs tests, and writes patch.txt.
