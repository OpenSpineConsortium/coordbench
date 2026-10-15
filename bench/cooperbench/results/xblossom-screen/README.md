# The X-Blossom screen, 2026-10-15

The user approved this screen at 17:24 UTC. It ran 12 runs: the xblossom, team and solo arms on go_chi 56 f1_f5 and click 2068 f5_f11, each with two seeds (tags xb1 and xb2). The runs started at about 18:44 and ended at 19:33 UTC. All of them came from coordbench main at 66c293a, which is #20 merged, with Sonnet 5.5 bench agents on the session route. The logs are in /root/cooperbench/logs/xb[12]-*, and the queue is /root/cooperbench/logs/xb-queue.jsonl.

## Result

| arm | passes | tokens (sum of 4) | tokens net of the inherited prompt | cost at list price | wall s (sum) |
|---|---|---|---|---|---|
| solo | 3 of 4 | 8,688,202 | 3,412,907 | $4.67 | 1,675 |
| team | 4 of 4 | 21,272,374 | 6,419,482 | $8.06 | 1,607 |
| xblossom | 4 of 4 | 26,874,563 | 9,001,803 | $9.78 | 1,749 |

These are the ratios of xblossom over team. Below 1 means xblossom used less.

- Tokens: 1.26 overall, and 1.40 net of the inherited prompt.
- Per run: 1.55 and 1.51 in seed 1 (go_chi, click), and 1.20 and 0.89 in seed 2.
- Cost: 1.21.
- Wall time: 1.09.

xblossom lost no pass that team kept. It used fewer tokens than team in only one of the four runs, and more in total.

What the arm did:
- The agents wrote 4 to 6 fact files per run under /workspace/shared/facts and looked them up by blob hash before reading a file.
- That lookup and its bookkeeping cost more than it saved on these two pairs.
- One xblossom agent (xb1, go_chi, agent1) met Claude Code's "Blocked: sleep" guard once.
- The audit found no harness error in any of the 12 runs: no full disk, no lost agent, no respawn. Every agent ran at effort xhigh, with a first-call context of about 81,000 tokens.

## The decision

The rule in trial-order.md, step 2, is worded "if xblossom uses no fewer tokens than team on both pairs and both seeds, or loses a pass team keeps, it stays out". Read literally, one cheaper run (xb2, click) lets xblossom in. The intent, as the thread put it when the user chose "All six arms" at 18:36, is "X-Blossom on all 43 if the screen shows it saves tokens without losing a pass". It does not save tokens: it costs 26% more tokens and 21% more dollars in total.

So xblossom stays out of the full run. The user confirmed this on the decision card at 19:36 UTC, choosing "Leave it out".

## A harness fix found here

The solo agent of xb1 click opened its reply with its run token, minus the run id's random suffix. The matcher read that token as another run's, so finish counted no tokens for that agent. The fix is in the hardening pull request: such a token no longer bars the match by the prompt file. The run's counts were then filled in from the transcript the fixed matcher finds (matched_by "named"), and solo_session_outcome.json records this.
