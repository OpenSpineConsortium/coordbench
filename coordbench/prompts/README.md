# The judge's prompts

coordbench/judge.py sends one of these files as the whole system prompt
of a judge call and the pair's material as the user turn;
coordbench/annotate.py sends causes-v1.txt for its optional drafts. Each
output in judge.json names the variant it used (prompt_variant), the file
and the file's SHA-256, so a label can always be traced to the exact text
the model saw. A changed prompt is a new variant under a new name, never
an edit of a file whose labels already exist.

| File | Variant | What it is |
| --- | --- | --- |
| commerrors-appF-7.txt | appF-7 (default) | CooperBench's App F prompt (PDF p.28) word for word, quotes and bullets in ASCII, with two changes: the fourth bullet is coded C3a where the paper prints a duplicate "C4a" (its own evidence rule and taxonomy note call it C3a), and a last line restates App F's Output sentence as a JSON schema. |
| commerrors-appF-8.txt | appF-8 | appF-7 with one bullet added, "C5 Plan drift / unilateral deviation", to recover Fig 6(d)'s category, which the printed prompt lacks. C5 is coordbench's code; the paper gives none. |
| symptoms-r1.txt | symptoms-r1 (default) | coordbench's reconstruction of App G's symptom rubric, which the paper does not publish: Table 1's caption and its ten meanings, App H's example of each symptom (quotes, arrows and dashes in ASCII), App G's evidence sentence, its decision rule (L1695-1700) and its instruction to emit a primary and secondary symptoms with evidence, all verbatim, with a tie-break for the confusion App G names (unverifiable claims against work overlap) and an output schema. |
| symptoms-r1-tree.txt | symptoms-r1-tree | symptoms-r1 with "Information asymmetry" added, a name CooperBench's website lists in its failure taxonomy tree but not in Table 1; its definition is coordbench's. Never counted as one of Table 1's ten. |
| speechacts-v1.txt | speechacts-v1 | One-sentence definitions of Plan, Question, Answer, Update and Ack, coordbench's own: the paper counts these acts (Fig 5c) but gives no method. |
| emergent-v1.txt | emergent-v1 | Sec 6.4's definitions of role division, resource division and negotiation, verbatim, and the website's git-failures.json descriptions of blame and sagas. The paper counts none of them; the labels are coordbench's. |
| causes-v1.txt | causes-v1 | Table 2's definitions of Expectation, Commitment and Communication, verbatim, for a draft the human reviewer never sees and the tally never counts. |
