# Which of the options to try, and in what order

Written 2026-10-15 18:15 UTC for the owner's question of 17:53 ("all of these seem interesting and I have no idea which one may work"). It weighs everything in this folder against what the step of 8 pairs measured. It draws on off-the-shelf.md and rag.md, the deep research on native git plus DeLM (research-git-delm.md), and the review of coordbench's harness (harness-review.md, in this folder).

## What the step says the headroom is

- On these 8 pairs there is no curse of coordination to remove. Every two-agent setting passed 6 of 8, against 5 of 8 for one agent. Nothing is significant at n = 8.
- What two agents cost is tokens and time. They used about twice one agent's tokens. Symphony's arm was the slowest, at a median 529 s against 296 to 335 s, with 31% of its agent time blocked waiting on the other agent.
- So an X-Blossom-style idea can help here in only two ways. It can cut tokens by reusing what one agent already checked, or cut waiting by letting agents work on disjoint pieces without stopping. A gain in pass rate needs pairs where coordination fails, and those only a larger sample can show.
- Neither the research nor any source found anyone who has measured reuse of verified results on CooperBench. Every option below is untested on this benchmark, so each one is a screen first.

## The order

1. **Fix the harness's blocking problems before any run grows.** This is not optional, because every later number depends on it. The review confirmed 30 of 32 findings, 10 of them blocking. One of the blocking ones, the unmeasured-agent count, is fixed in #19. Six more need code before a larger run:
   - nothing checks free disk;
   - a lane that comes back re-runs a finished pair;
   - a respawned agent's tokens and time are counted wrongly;
   - a lost subagent is recorded as a 45-minute timeout;
   - this session's permission refusals are scored as the arm's own failures;
   - the session route drops an arm's plugins and options without saying so.

   The remaining three are caveats on what the git arms can claim. One should-fix finding belongs with the six: every arm runs in the same order on every pair.
   - The largest problem is in the measurement. Each bench agent in this session's route re-reads a fixed prompt of about 80,000 tokens on every call, inherited from this session. That is 74 to 76% of every arm's input and 35 to 43% of its dollars.
   - The comparison between arms survives that, but the absolute cost does not. For numbers that will go into the grant, the full run should use standalone agents in GitHub Actions, as CooperBench runs them.

2. **The X-Blossom screen: the four moves together, 12 runs.** You approved it at 17:24, and it runs now. The arms are xblossom, team and solo on go_chi 56 (the only pair that split the arms) and click 2068 (where team's patches conflicted), with two seeds each. xblossom is the team arm plus one prompt section, so any difference comes from the four moves. It reads three things:
   - fact files written and reused;
   - tokens and time against team;
   - whether any pass is lost.

   The rule: if xblossom uses no fewer tokens than team on both pairs and both seeds, or loses a pass team keeps, it stays out of the full run.

3. **The full suite: the 43 gradable pairs, with xblossom in it if step 2 passes.** The card in the thread asks you how and where. Pairs go in the stratified order, so a run cut short is still a balanced sample of 16, 24 or 32 pairs. This is the only step that can show a curse, and a cure for it, if either exists on these agents.

4. **If xblossom helps, find which move does it: one move at a time, 16 runs.** The moves are:
   - facts keyed to file content;
   - the disjoint file plan;
   - claims by compare-and-swap;
   - certificates in the done note.

   Each is a prompt section, so each test is a copy of the arm with three rules removed, on the same two pairs and two seeds. This tells you what to build into Symphony itself, instead of keeping a bundle.

5. **Build the winning move into Symphony's own arm (sdlc), the target of Aim 1.** Three candidates come from the step and the research:
   - Make the merge gate run the tests. Today it runs none, so a merge says nothing about the code. The review confirmed this, and it is the closest match to Symphony's required check on GitHub.
   - Let the second agent keep working instead of blocking in wait_for_message. That is the 31% of sdlc's time spent waiting.
   - Store checked facts as git notes, one notes ref per agent, keyed on a tree of the files the fact depends on, plus the command and an environment digest. Per-agent refs merge without coordination. A single shared notes ref loses notes when two agents write at once. A key on single blobs is weaker than DeLM's receipts. git-test and Bazel's action cache are the working precedents.

6. **Native git tools against conflicts, only if the full run shows conflicts that matter.** Candidates:
   - a claim that cannot be falsely won, made with a unique commit and a create-only lease;
   - a check of two branches with `git merge-tree --write-tree` before submitting, reading its output rather than its exit code, which also signals errors;
   - a shared rerere cache;
   - a merge driver that accepts only pure insertions.

   On the 8 pairs, conflicts decided little. 4 of the 24 two-agent passes came through CooperBench's lead-alone fallback, and Symphony's arm submits the integrated work, so it cannot conflict at scoring.

7. **DeLM's check receipts as hub tools, only if steps 4 and 5 show that reuse pays.** A receipt binds a command's result to the exact files it read, and any change to them voids it. The design can be re-implemented in coordbench's hub. DeLM's board is 684 lines of standard-library Python under the MIT license. Its harness cannot be used as it stands: it needs a stored setup-token file, which your no-stored-credential rule forbids. Its plugin is macOS only, Rust, and limited to two agents.

8. **RAG over your own code or literature belongs to the grant's science, not to CooperBench.** Each CooperBench task is one repository, and the agents already search it. rag.md has the options for Aim 3's literature.

## What you would decide, and when

- Now: the full suite's size and route (the card in the thread).
- After the screen, about an hour after it starts: whether xblossom joins the full run. I decide that by the rule in step 2 and tell you.
- After the full run: whether to spend about 16 runs on step 4, and whether step 5's changes to Symphony's arm go ahead. These are your calls, since each spends usage.
