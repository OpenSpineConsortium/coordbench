# Review of coordbench's harness before the full run

A review workflow of 2026-10-15 (16:45-18:10 UTC) read coordbench as the step of 8 pairs ran it (main at 43c2e3d and pull request #19), in four dimensions: measurement, git plumbing, the arm API and the session route. Each finding was then checked by an adversarial verifier that tried to refute it. 30 of 32 survived. Some were already fixed in #19 by the time they were checked (the unmeasured-agent count in summary.py, the named transcript tier); the rest are open.

Severity is the finder's; the verifier's notes say where it overstated. Line numbers are those the verifier confirmed or corrected.

## blocking, confirmed: Session route silently drops arm plugins, MCP servers and every run option, and still records the arm as run

arm-api, bench/cooperbench/session/lane.py:92

Verifier: I could not refute the core of the finding. I reproduced it in-process with /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/xb-verify/check.py. It copies arms/coop to a scratch arm and sets name "git-delm", plugins ["delm-plugin"], an arm.json mcp_servers entry and an arm.json "options" block. No docker or bench was run, and the worktree is unchanged.  What reproduced: 1) lane.prepare_item builds the run.py prepare argv with no --option and no --max-turns (lane.py:92-95). Parsed by run.py's own parser() and settings_from, it gives options {} and max_turns None (run.py:3750). The arm.json "options" block is ignored. A queue item has no options field (queue.py:131-134). 2) new_plan records plugin_entries ['delm-plugin'], the delm mcp_servers and arm_plugin True, with warnings [] (run.py:2323-2325, 2340). resolve_plugins resolves the plugin to /opt/coordbenc ...

## blocking, confirmed: The scorer cannot tell integrated work from duplicated work, so git arms read as conflict-free by construction

git-plumbing, bench/cooperbench/report.py:815

Verifier: I could not refute it. The code paths, the step-8 records and an in-process run all support the finding. Two parts are overstated, noted at the end.  Code paths (all line numbers confirmed in /home/user/coordbench-step): - CooperBench 63b9d44 (blobless clone, since deleted): src/cooperbench/eval/sandbox.py filters test files at 139-140. At 181 it compares the filtered patches, and at 182-212 it returns status 'identical' / 'skip-merge-identical' after testing patch1 alone. Its own comment at 168-170 expects this to happen when agents have fully merged each other's work. - coordbench/cascade.py:338-347 (naive_step) merges the two branches. Identical branches merge cleanly, so line 306 sets has_naive_merge_conflict=False. - bench/cooperbench/report.py:815-828 (naive_conflict) prefers that flag and otherwise maps 'identical' to False (line 828). - coordbench/export.py:1562-1574 (naive_confl ...

## blocking, confirmed: The sdlc merge gate runs no tests, and naming a required check would block every merge

git-plumbing, coordbench/vendor/sdlc_protocol/adapters/bare_git.py:150

Verifier: I could not refute the mechanics. I reproduced them, but the severity and the fix are overstated.  What I confirmed in /home/user/coordbench-step at 71feb15: - BareGitWorld.check always returns NO_CHECK, with the docstring "A bare repository runs no checks" (coordbench/vendor/sdlc_protocol/adapters/bare_git.py:150-152). NO_CHECK is "none" (gate.py:67). - The arm's POLICY sets required_check="" (coordbench/arms/sdlc/arm.py:99). With that value, require_green returns at once (gate.py:476-479). - arm.mark_ready calls require_green before the merge (arm.py:260). merge_commits is a plain `git merge --no-ff` plus a push, with no semantic step (bare_git.py:259-278). - If required_check is non-empty, require_green raises Refused "has not reported" on every call (gate.py:487-489), because check never answers anything else. The library's own Policy default is required_check="check" (gate.py:96), s ...

## blocking, confirmed: The run's bare remote enforces no ref rule: claim refs are not a CAS on the server, and main can be rewound

git-plumbing, coordbench/run.py:2424

Verifier: I could not refute it. The finding's core holds in /home/user/coordbench-step, and I reproduced it.  **Code.** - init_repository_script (coordbench/run.py:2424-2432) runs only `git init -q --bare`, `receive.denyCurrentBranch ignore`, `core.logAllRefUpdates always` and `touch git-daemon-export-ok`. - The server image (bench/cooperbench/setup.sh:66-68) starts `git daemon --export-all --enable=receive-pack`. - Nothing in coordbench/ or bench/ sets receive.denyNonFastForwards or receive.denyDeletes, or writes a hooks/update. The only receive.* hits are run.py:2431. - CooperBench upstream does the same (/root/cooperbench/src/cooperbench/agents/mini_swe_agent_v2/connectors/git_servers/docker.py:149). The gap is parity with CooperBench, not a regression of the runner.  **Reproduction.** I made a bare repo with the exact init commands, served it with a local `git daemon --enable=receive-pack` on ...

## blocking, confirmed: An agent whose transcript was not matched counts as zero tokens and $0, and the pair still counts as complete

measurement, bench/cooperbench/session/summary.py:74

Verifier: Confirmed against PR #19 as committed (HEAD bf67426). Someone else is editing the worktree with uncommitted changes to drivers.py, summary.py, tests and the step-8 result.json, pairs.csv and summary files. Those edits fix this exact finding: they add a "named" match rule, an unmeasured-agent path and backfilled tokens. So every check below ran on HEAD's files, copied out with git show and git archive. I modified nothing in the worktree.  1. The spawn prompt does not carry the token. At HEAD, lane.js:66-72 (benchPrompt) names only the prompt file (promptFile, lane.js:60-64) and asks the agent to copy the token from that file into its first text reply. The PLAN schema has a token field (lane.js:42), but benchPrompt never uses it. The token is only in the preface written to the prompt file (drivers.py:910-911 and the SESSION_PREFACE).  2. carries_token returns None for these transcripts. In ...

## blocking, confirmed: A respawned agent counts only its newest transcript, and its time includes the dead gap

measurement, coordbench/drivers.py:957

Verifier: The finding holds. I reproduced it from the real transcripts of sdlc-2068-f5_f11-a96d43 (lane-2, workflow wf_135fb070-e4a). Two of its line numbers are off by a few: the pick is at drivers.py:958 (`source = matches[0]`), not 957, and the sort is at drivers.py:1029, not 1026.  Code path: - `take_transcript` (drivers.py:945-966) copies and counts only `matches[0]`. - `find_transcripts` (drivers.py:1012-1030) sorts by match kind, then by mtime with the newest first (1029), so the newest "reply" transcript wins and every older one is dropped. Only `len(matches)` goes to `_session_outcome.json` (drivers.py:965-966, 1153-1167), and bundle.py:25 leaves that file out of the results. - The agent phase starts at `start_agents` in prepare (run.py:2868) and ends at the marker's mtime (drivers.py:940, run.py:3012-3013, 3030-3031). A re-touched marker therefore moves the end.  How the respawn happened ...

## blocking, confirmed: Subagents the harness lost are recorded as agent errors with a made-up timeout, and counted as model failures

measurement, bench/cooperbench/session/lane.py:211

Verifier: I reproduced the mechanism in-process, and the pilot's numbers match. Two things are overstated: the severity, and the claim that these pairs are "counted as model failures".  What reproduced (script at /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/verify-lane-dead/check.py): - lane.js:102-106 marks a rejected agent() call as dead. - lane.py:210-213 sets the dead agent's deadline to now-1. - For a subagent lost one minute in, SessionDriver.wait returns status Error with end_reason 'no .coordbench-done within 45 minutes' and ended_at None (drivers.py:931-935). The 45 comes from timeout_s, not from the time that actually passed. - error_kind() returns 'agent' (run.py:3367-3373). - With ended_at None, the agent phase ends at finish's now() (run.py:3007-3014, 3030-3032). - verdict() returns 'error' even when both_passed is True (run.py:3230-3236). - summary ...

## blocking, confirmed: A lane that comes back re-spawns bench agents on a run that already ran, and the run is then scored as clean

session-route, bench/cooperbench/session/lane.py:145

Verifier: I could not refute it. The code path is real and I reproduced it with my own in-process check, which builds its handles with the real SessionDriver.  Code path, in /home/user/coordbench-step: - queue.py:150-153 returns the worker's own running item whenever finishing_at is unset. - lane.py:145-147: an item with prepared_at and run_dir goes straight to announce(plan_from_disk(item)). - plan_from_disk (lane.py:114-126) reads only plan.json and the prompt files. It never looks at markers, patch.txt, git state or transcripts, and it keeps each handle's started and deadline exactly as drivers.py:913-916 set them at the first prepare. - finishing_at is written only by a non-detached finish (lane.py:328-329). The detach in step 1 of the relay starts that finish, so the field stays unset when the finish relay dies before step 1. - lane.js:107 has no catch, and the Workflow reference (workflow-au ...

## blocking, confirmed: Refusals by the session's permission classifier and Bash guard are scored as the arm's own result; claude-cli agents never meet them

session-route, coordbench/drivers.py:930

Verifier: I could not refute the core of the finding. I reproduced it in code and in the transcripts. Some details are wrong, and the "uneven" part is overstated.  Code path (in /home/user/coordbench-step, which I left unmodified; git status is clean): - drivers.py:930 sets Submitted. Only a missing marker changes that (drivers.py:931-937). - take_transcript (drivers.py:945-962) reads only token usage, through fill_from_transcript. - record_session_outcome is at drivers.py:1153-1166, not 1130-1139 as the finding says. It has no field for tool errors. - lane.js:105 counts as returned any truthy result, so `{finished:false}` counts. - lane.py:204-209 touches the marker for every returned agent whose deadline has not passed. - run.py:507-512 agent_failed is true only for status Error. So a Submitted agent with an empty patch is a valid sample and is scored as a fail.  In-process check (/tmp/claude-0/ ...

## blocking, confirmed: Disk-full is neither prevented nor detected; two counted step-8 runs met ENOSPC, and only spoiled runs that failed were rerun

session-route, bench/cooperbench/session/lane.py:148

Verifier: I could not refute it. The code defect is real, and the two go_chi runs are as the finding describes them. Several details are wrong, though, and calling it "blocking" for the step-8 verdicts goes too far.  What I checked in the code: - command_prepare (bench/cooperbench/session/lane.py:135-172) gates only on the queue state and wait_for_image (lane.py:148). wait_for_image (lane.py:66-72) checks only that the image is labelled. - Nothing in lane.py, coordbench/run.py or coordbench/drivers.py checks free disk. A grep for disk_usage, statvfs, ENOSPC and "no space" finds only tests of the harness's own write failures (tests/test_coordbench_run.py:1007-1065) and the Actions route (disk_free_bytes in bench/cooperbench/actions/survey_task.sh:37 and free_disk.sh). - finish_pair (lane.py:294-319) reads only run.py's last JSON line. - SessionDriver.wait (coordbench/drivers.py:930-951) sets status ...

## should-fix, confirmed: A refused claim leaves no cause in the record, and exit 2 means a lost race, bad words, a 401 and an unreachable hub alike

arm-api, coordbench/mcp.py:209

Verifier: I could not refute it. The finding reproduces in /home/user/coordbench-step (HEAD 71feb15). Two of its line numbers are off, but the substance holds.  How I checked: /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/verify-exit2/repro.py starts an in-process hub with hub_module.serve_in_background, makes two team runs (one with agent tokens, one without), runs cli.py as a subprocess the way an agent's shell does, and reads hub.events() and events.jsonl after each call.  Results: - Lost race (agent1 claims agent2's task): exit 2. - Missing task_id, through `call` with {} and through `alias-run` with no words: exit 2. - A word too many: exit 2, from the client, and no event. - Unknown argument: HTTP 400, exit 2, no event. - No token on a run made with agent tokens: HTTP 401, exit 2, no event. - Unreachable hub: exit 2, no event. - Nonexistent task: exit 1.  T ...

## should-fix, confirmed: The copied shared volume is walked as pair directories: a result.json an agent writes there inflates summary.json and judge and export runs

arm-api, coordbench/runconfig.py:302

Verifier: I could not refute it. The finding reproduces, and every code path it cites checks out in /home/user/coordbench-step.  Code paths: - runconfig.py:302: run_totals walks the whole setting directory with `Path(setting_dir).rglob("result.json")`. Every JSON object it finds adds 1 to count (runconfig.py:304-308), even `{}`. - run.py:3546-3562: copy_shared_volume runs `docker cp <container>:/workspace/shared/. <run_dir>/shared`, which copies the volume recursively into the pair directory. It runs whenever the plan has a volume and containers. - run.py:3567-3573: teardown always calls copy_shared_volume first, with or without --keep. - run.py:3091-3110: finish_pair calls teardown. - run.py:4036-4049: command_finish then calls refresh_summary. - run.py:3965-3976: refresh_summary calls run_totals(run_dir.parents[2]), so the walk covers <pair>/shared. It also uses totals["wall_time"] as total_time ...

## should-fix, confirmed: The board archetype the notes recommend for post_fact appends and emits in separate lock holds and keeps no text

arm-api, coordbench/examples/board/arm.py:45

Verifier: The finding holds. One part overstates the impact.  The code matches the claim: - In coordbench/examples/board/arm.py:45-47, the append runs inside `with call.run.lock` and `call.run.emit("notice", call.agent, chars=len(text))` runs after the lock is released. The generator template has the same lines at coordbench/new_arm.py:179-181. The same code is on main at 5028999, so PR #19 did not introduce it. - Run.emit takes the lock again for its own hold (coordbench/arm.py:296-309). Another request thread can therefore append and emit between the two holds. The hub serves each request on its own thread (coordbench/hub.py:132, 900, ThreadingHTTPServer). - kit.deliver shows the safe pattern: next_seq, the append and the emit all happen in one hold, with the text as `content` (coordbench/kit.py:154-159). The lock is a Condition over an RLock, so emitting inside the hold works. - The notice even ...

## should-fix, confirmed: task_list snapshots under the lock but writes the volume mirror after releasing it, so an older snapshot can overwrite a newer one and unlink new tasks

arm-api, coordbench/arms/team/arm.py:402

Verifier: I could not refute it, and I reproduced the race twice.  **What the code does.** In /home/user/coordbench-step/coordbench/arms/team/arm.py, task_list takes the snapshot and the closed flag under run.lock (395-399). It calls mirror_if_shared after the lock is released (402).  mirror_task_list (533-549) does three things: - It rewrites one file per task in its own snapshot. - It unlinks every non-underscore .json whose id is not in that snapshot's live set (538-545). - It rewrites _index.json (547) and _log.jsonl.  Nothing orders two mirror writes against each other.  **Why two lists can overlap.** The hub serves every request on its own thread: HubServer is a ThreadingHTTPServer (coordbench/hub.py:132 and :900). Hub.call (hub.py:421) goes through mcp.call_tool to the handler (mcp.py:206), and that path takes no run-level lock. So two agents' coop-task-list calls run their mirror writes at ...

## should-fix, confirmed: Team claims can never be released or reassigned, though the lead's prompt tells it to reassign

arm-api, coordbench/arms/team/arm.py:212

Verifier: The mechanics hold, and I reproduced them in-process. But as a should-fix in team/arm.py the finding is wrong: this behaviour is a deliberate, test-pinned port of CooperBench 63b9d44. Keep the finding as a requirement for the git-DeLM subclass, not as a fix to the team arm.  Code at PR #19, head 71feb15: - Board.claim (coordbench/arms/team/arm.py:212-223) refuses any owner other than '' or the caller (220-221). It always sets owner=by and status="in_progress" (222). - Board.update (225-240) is owner-only (231-232) and never writes owner. - Owner is written only at create (199-208, 378, seeding at 821/824) and at claim (222). - TeamArm.tools (827-828) returns task_create/claim/update/list (task_tools, 433-461), the protocol tools and messaging. None of them releases or reassigns. - The lead prompt says "either reassign or unblock it with `coop-send`" (622-624).  Scratch run through hub.Hu ...

## should-fix, confirmed: Patch formation takes whatever is in the tree: a worktree becomes a gitlink, a binary file fails the whole patch, and patch.txt gets committed

git-plumbing, coordbench/arms/coop-git/arm.py:64

Verifier: I could not refute it. I checked the code and reproduced every mechanism the finding names, using git 2.43 in scratch repositories under /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/verify-coopgit-patch/scripts (repro.sh, repro2.sh, repro3.sh). The large clones have been deleted.  Code facts (in /home/user/coordbench-step): - coordbench/arms/coop-git/arm.py:64 and :76 are prompt text: the git block requires `git add -A` and then `git diff team/main..HEAD > patch.txt`, with no --binary and no exclusions. The text is CooperBench's _git_block, copied word for word. - coordbench/run.py:2630-2635 (keep_marker_out_of_git) writes only DONE_MARKER (".coordbench-done", drivers.py:105) into .git/info/exclude. - It is called only from copy_repository_out (run.py:2626). That runs only when plan["host_workspace"] is set (run.py:2825-2826), so the claude-cli path ex ...

## should-fix, confirmed: coop-git's patch is a diff against a moving ref, team/main, not the base commit

git-plumbing, coordbench/arms/coop-git/arm.py:76

Verifier: I could not refute the finding. The mechanism and every code citation hold, but the defect only appears once something moves main, and nothing in the coop-git arm as written does that.  Code checked in /home/user/coordbench-step (PR #19 changes no file under coordbench/arms): - coordbench/arms/coop-git/arm.py:48 says "Base reference: `team/main` (pristine starting state)". - coordbench/arms/coop-git/arm.py:76 builds the patch with `git diff team/main..HEAD > patch.txt`. - The merge lines end in `|| true` (arm.py:100). - The skill repeats the same diff (coordbench/arms/coop-git/skills/coordbench-coop-git/SKILL.md, "Submitting"). - The sdlc arm diffs against the base sha instead: coordbench/arms/sdlc/arm.py:427 `git diff {base} team/main > patch.txt` and :431 `git diff {base} HEAD > patch.txt`. Its protocol_block (sdlc/arm.py:461-474) fills in base from queue.base_ref.  Nothing holds main  ...

## should-fix, confirmed: Wall time includes provisioning contention and the wait for finish to run

measurement, coordbench/run.py:3102

Verifier: I could not refute the core of the finding. I reproduced its main evidence from the committed result.json files. Some secondary details are wrong or overstated.  Code path, confirmed: - In coordbench/run.py:3101-3102, `ended = now()` is taken right after wait_agents returns, and cooperbench_result_fields computes duration_seconds as ended minus plan["started_at"] (run.py:3350, 3377). - The per-agent "agent" phase uses agent_ended (the marker's mtime, run.py:3010-3017, 3030-3032). duration_seconds does not use it. - In the session route, prepare and finish are separate processes (command_finish, run.py:4036-4042). SessionDriver.wait finds an existing marker on its first look, then settles each transcript for at least 5 s (drivers.py:919-944). So `ended` is whenever finish happens to run, plus that settle time. - The run.py docstring (78-80) says duration_seconds runs "to the agents' end". ...

## should-fix, confirmed: A fixed per-call prefix of about 80K tokens is 81-86% of every agent's input, and API calls are never recorded

measurement, coordbench/drivers.py:971

Verifier: I reproduced the finding's core claims, so it stands. A few details are off and are listed at the end.  1. **Prefix size and share: reproduced.** I re-ran /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/measure/prefix_share.py over /root/.claude/projects and got the same numbers. I also wrote my own check, /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/verify-prefix/check.py, which dedupes by message id and sums input + cache read + cache write.    - It found 54 bench agents.    - The first-call input has a median of 78,485 to 78,500 tokens in each arm.    - First-call input times calls, over summed input, is: solo 0.85, coop 0.82, coop-git 0.81, sdlc 0.82, team 0.84.    - Every later call's input was at least the first call's (0 exceptions), so nothing was compacted and the first call really is a prefix that every call c ...

## should-fix, confirmed: Output tokens are undercounted by about a fifth, by a different amount in each arm, and summary prices the lower bound as the count

measurement, coordbench/drivers.py:512

Verifier: I could not refute the finding. The mechanism and the per-arm bias both reproduce. The title overstates the size: "a fifth" is the share of messages missing a final count, not the share of output tokens missed.  What the code does: - coordbench/drivers.py:512-544: transcript_usage keeps the largest output_tokens seen on each message id's lines. A message is final only if one of its lines has stop_reason (lines 532-533). If any message is not final, output_final is false (line 543). - drivers.py:973-990: fill_from_transcript handles this correctly. It leaves output_tokens and cost as None and stores output_tokens_lower_bound and cost_lower_bound (lines 984-986). - bench/cooperbench/session/summary.py:83: summary.py drops that distinction. It puts the lower bound in "output". - summary.py:89-93 and 128-138: cost_of prices that output into cost_usd. - summary.py:258-281: the per-arm cost me ...

## should-fix, confirmed: Model usage by a hub tool or any helper is never counted

measurement, coordbench/drivers.py:1022

Verifier: I could not refute it. The mechanism is real and I reproduced it, but it is latent: no arm calls a model today, as the finding itself says.  Code evidence, all in /home/user/coordbench-step at 71feb15: - drivers.py:1020-1023: find_transcripts keeps a .jsonl only when "subagents" is in its path parts and it was modified on or after `since`. - drivers.py:1063-1074 (carries_token): a file must carry the agent's run token, or name the agent's prompt file, to count. drivers.py:1067 returns None for a file that names another agent of the run. - drivers.py:958-962 (take_transcript): only matches[0] is copied and counted through transcript_usage and fill_from_transcript. - drivers.py:913-924 (SessionDriver.start): job.mcp_servers and the plugins are never used. - Hub events carry no usage fields. The Run.emit contract at arm.py:237-275 lists seq, t, kind, agent, tool and timings only, and hub.py ...

## should-fix, confirmed: The time limit is not enforced: an overrun with a marker is scored normally, one without is an Error that is rerun; claude-cli kills at the limit and keeps the sample

session-route, bench/cooperbench/session/lane.js:102

Verifier: I tried to refute the finding and could not; every part of it held up. I checked it against the code in /home/user/coordbench-step and with my own in-process check, /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/verify-deadline/check_deadline.py. That check uses no docker and starts no agents. The worktree is unchanged.  Code paths: - **No time limit at spawn.** lane.js:102-104 calls agent(benchPrompt, {label, schema, model}) inside parallel(), which waits for every agent. The Workflow agent() hook has no timeout option at all, so the lane waits as long as a subagent runs. - **Late markers are accepted.** finish runs only after parallel() returns (lane.js:107). SessionDriver.wait (drivers.py:926-937) accepts a marker on its first look. It sets ended_at to the marker's mtime and never compares that with handle["deadline"]. run.py has no overrun check eith ...

## should-fix, confirmed: Each bench agent re-sends an 81.7k-token prompt inherited from the session on every API call, 75-79% of all input, so token ratios measure call counts

session-route, bench/cooperbench/session/lane.js:103

Verifier: I could not refute this finding. The numbers reproduce exactly, and an independent check backs the mechanism. A few details are slightly off.  What reproduces: - I re-ran turns2.py over this session's subagent transcripts (/root/.claude/projects/-home-user/b761d652-.../subagents). It finds the 76 bench agents and gives the same figures:   | arm | agents | calls per agent | median first-call prompt | share of input |   |---|---|---|---|---|   | solo | 8 | 16.8 | 81,671 | 0.75 |   | coop | 16 | 16.1 | 81,672 | 0.79 |   | sdlc | 18 | 18.1 | 81,677 | 0.78 |   | coop-git | 18 | 18.9 | 81,677 | 0.77 |   | team | 16 | 19.0 | 81,668 | 0.78 | - My own script (scratchpad/gitdelm/verify-tokenfloor/prefix_check.py) checks the rest across all 76 agents:   - The first call is 80,969 to 81,689 tokens.   - Every later call's input is at least the first call's, so the prefix really is re-sent on every ca ...

## should-fix, confirmed: Bench agents inherit the session's effort (xhigh) and it is recorded nowhere, so runs from another session or after an effort switch are not comparable

session-route, bench/cooperbench/session/lane.js:103

Verifier: I could not refute it. The finding holds in the code and in the transcripts.  1. Effort is not pinned for bench agents. In /home/user/coordbench-step/bench/cooperbench/session/lane.js:103 the bench agents are spawned with `{ label, schema: BENCH, model: BENCH_MODEL }` and no effort. The prepare relay at lane.js:89 and the finish relay at lane.js:107 do pin `model: 'haiku', effort: 'low'`. The args comment (lane.js:9) has no effort argument.  2. I re-ran the evidence myself. Running /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/session-route/effort.py over /root/.claude/projects/-home-user gives:    - sdlc 18, team 16, coop-git 18, solo 8, coop 16. That is 76 transcripts.    - Every one is `('xhigh','xhigh','claude-sonnet-5-5')`.  3. The effort field records each agent's own effort, not the session's. My own tally (/tmp/claude-0/-home-user/b761d652-4496- ...

## should-fix, confirmed: Arms always run in the same order on every pair, so host events, load and usage-window cut-offs fall on arms systematically

session-route, bench/cooperbench/session/queue.py:127

Verifier: I could not refute it. The mechanism is in the code, I reproduced it, and the step-8 data shows it. Two pieces of the evidence are wrong or overstated, listed below.  Code: - queue.py:124-138 queues each pair's arms in the --arms order (line 127 is `for arm in arms:`). - queue.py:154-161 hands out the first pending item. - queue.py:137 appends every new item after all the existing ones. - The docstring at queue.py:28-31 and session/README.md:30-33 make pair-major order deliberate, but nothing varies the order of arms within a pair. - The repository's other route already guards against exactly this. actions/plan.py:8-11 says "the arm order rotates by pair index, so no arm is always first on an account's fresh window or always last on its tired one", and actions/plan.py:144-145 does it with `rotated = arms[index % len(arms):] + arms[:index % len(arms)]`. So the session route is the one wit ...

## nit, confirmed: new_arm.py --check cannot exercise a team or coop-git subclass, and an arm's needs are read from two places that can disagree

arm-api, coordbench/new_arm.py:377

Verifier: I tried to refute the finding and could not; both halves reproduce. Tests ran on a git-archive copy of /home/user/coordbench-step HEAD 71feb15 under the scratch dir. Neither worktree was touched, and no docker or run.py commands ran.  (1) --check cannot exercise an arm with needs, or one without the board tools. - check() calls hub.create_run(arm, CHECK_FEATURES, CHECK_SPECS, None, CHECK_RUN_ID) with options=None (coordbench/new_arm.py:377). - It then calls post_notice and read_board unconditionally (new_arm.py:385-389). - Hub.create_run refuses when unmet_needs is non-empty (coordbench/hub.py:227-230). unmet_needs reads only arm.needs and empty options (coordbench/arm.py:87-90). - Results of `python3 coordbench/new_arm.py --check coordbench/arms/<a>`:   - team: "check failed: the team arm needs shared_volume, which the run's options do not provide"   - coop-git and sdlc: the same, with  ...

## nit, confirmed: git_log.txt names no claim ref, drops claims made at the base commit, and logs notes commits as the agents' commits

git-plumbing, coordbench/kit.py:321

Verifier: I tried to refute this and couldn't: every mechanical claim reproduced. I ran the code in-process on a scratch bare repo with git 2.43.0 and imported nothing that writes into the worktree, so /home/user/coordbench-step is unchanged. The repo had core.logAllRefUpdates=always, as run.py:2430-2433 sets up, and held these refs: - refs/heads/main at the base - refs/heads/agent1 and refs/claims/unit-8 at agent1's commit - refs/claims/unit-9 at the base - refs/notes/facts, where agent1 added a 150-line note and agent2 then overwrote it with a 1-line note (`git notes add -f`)  I called kit.git_log twice, once on the directory in place and once through file:// (the fetched_copy path, which fetches `+refs/*:refs/*` at kit.py:376). Both gave the same output: 1. GIT_LOG is at kit.py:321 and uses `--decorate=short`. Commit 50cec8e was decorated only `(agent1)`, with no refs/claims/unit-8. 2. log_of a ...

## nit, confirmed: The overlap and standing probes need git merge-tree --write-tree and degrade silently on an older git

git-plumbing, coordbench/arms/sdlc-watch/arm.py:166

Verifier: I could not refute it: I reproduced the failure on Ubuntu 22.04's own git. The code is as the finding says. bare_git.py:290-295 (read_standing) and sdlc-watch/arm.py:166-174 (trial_merge) both run `git merge-tree --write-tree --name-only --no-messages`. Exit code 0 means clean and 1 means conflicting. Any other code gives Standing(UNKNOWN), from line 295, or None, from line 174, and the git error message is thrown away.  The user reads only canned text. gate.py:690 prints "Against main at X: unknown." and sdlc-watch/arm.py:320-321 prints "Whether your branch and theirs merge cleanly could not be told." Neither says why. Nothing checks the git version first: run_git (bare_git.py:61-70) only catches a timeout and a missing git, and searching the repository found no git version check on the hub's side. The only `git --version` is in bench/cooperbench/build_task_image.sh:38, which runs insid ...

## nit, confirmed: An agent's phase and deadline start at prepare, not when its subagent starts

measurement, coordbench/run.py:2868

Verifier: I could not refute it. The code works as the finding says, and I reproduced the numbers with a stricter matcher of my own.  Code path (in /home/user/coordbench-step): - start_agents stamps agent["started_at"] = now() in run.py:2868. That runs inside prepare, before the plan is saved (run.py:2997-2998). - The session driver sets started = time.time() and deadline = started + timeout_s at drivers.py:913-916. - wait_agents measures the "agent" phase from that started_at to the marker's mtime, at run.py:3030-3032 (via agent_ended, run.py:3012-3013). summary.py:154 sums these spans into agent_seconds_sum. - Nothing later corrects the start. A grep finds no queued_at, startedAt or transcript-based start in coordbench/*.py or session/*.py. - What happens after the stamp, in order: run.py prepare prints its plan and exits. Then lane.py marks the item in the queue and prints the plan (lane.py:162 ...

## nit, confirmed: The marker is advisory: the patch is read when finish runs, and agents keep acting on their workspace after the marker

session-route, coordbench/drivers.py:938

Verifier: I could not refute it. The mechanism is in the code, and an in-process check reproduced both halves. Severity nit is right: no counted score was changed.  Code paths (coordbench-step at HEAD 27f64f8; the cited lines are unchanged from 71feb15): - drivers.py:937 sets `ended_at = marker.stat().st_mtime`. - drivers.py:938-940 then read `<workspace>/patch.txt` at the moment `wait()` runs. - `wait()` is called from run.py:3027 (`wait_agents`) inside run.py `finish_pair` (3091-3104). The patches scored are `outcomes[...].patch` (run.py:3245-3252, 3637-3640). - run.py finish is reached through bench/cooperbench/session/lane.py `finish_pair` (294-305). lane.js:102-107 runs it only after `await parallel(...)` of every bench agent, so the patch is read after every subagent has returned, not when the marker appeared. - The prompt promises "Your run ends when it appears; nothing you do after that co ...

## should-fix, refuted: A response that arrives after the requester's wait ended can never be read; the undo puts it back where no tool looks

arm-api, coordbench/arms/team/arm.py:406

Verifier: The mechanics are real and I reproduced them. The finding is still refuted, because this is not a should-fix defect: the arm copies CooperBench 63b9d44 exactly, the project's parity rule forbids the proposed fix without a stated reason, PR #19 does not touch this code, and no recorded team run ever used request or respond.  What I reproduced, with /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/xb-respond/probe.py, using the repository's own test helpers and no bytecode written: - A request with wait=0.1 exits 5. The peer's respond then returns exit 0. - agent2's receive_messages, peek_messages and pending then print [], 0 and [], while board.responses still holds the body. - A request with no wait, answered by its peer, leaves its response in board.responses too. - No tool reads a response by id. The run exposes 13 tools: broadcast, list_agents, peek_mes ...

## should-fix, refuted: No fetch the harness configures carries notes or claim refs, and an arm has no hook to configure the agents' clones

git-plumbing, coordbench/run.py:2707

Verifier: The git facts hold, but the defect the finding rests on, that an arm has no hook to configure the agents' clones and can only do it through its prompt, is false. The fix it proposes is not needed for the default driver.  What holds: - git_setup_command runs `git remote add team` with git's default refspec (coordbench/run.py:2707). This copies CooperBench 63b9d44 on purpose (docstring at run.py:2695-2699), so it is parity, not an oversight. - The gate also fetches only branches: it clones at bare_git.py:195 and fetches origin at :215, in coordbench/vendor/sdlc_protocol/adapters/bare_git.py, which is vendored code. - I reproduced the git behaviour in a scratch repository:   - remote.team.fetch is `+refs/heads/*:refs/remotes/team/*`.   - After a1 pushed refs/notes/facts, a2's `git fetch team` brought no refs/notes.   - The unforced `refs/notes/*:refs/notes/*` fetch was rejected as non-fast- ...

