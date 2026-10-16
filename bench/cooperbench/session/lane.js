// One lane of a session-driven coordbench run, as a Claude Code Workflow
// script: take the next item of the shared queue, prepare it, spawn one
// subagent per bench agent, finish it, and go on until the queue is
// empty. Several lanes run at once as several workflows of the session
// (a workflow runs two agents at a time on a four-CPU host, a pair's
// two). Each lane's agents: a small one that runs lane.py prepare and
// relays its JSON, the bench agents (the pre-registered model), and a
// small one that starts the detached finish and waits for its verdict.
// args: {queue, worker, log_dir, max_items, bench_model, effort, root, resume}
// root is the coordbench checkout whose lane.py and run.py the lane runs,
// and must be given: a default checkout can hold older code than the
// arm being run, and the lane would run that code without saying so.
// bench_model is the model the bench agents run as and must be the one
// the queue's items name (the plan's model), since result.json records
// the queue's. effort is the reasoning effort the bench agents run at
// (low, medium, high, xhigh or max); left out, they inherit the
// session's, which audit.json records per agent, so a run carried on
// from another session names the earlier runs' effort here, or its
// agents think with a different budget than theirs did. Any other value
// stops the lane before it takes an item, since the runtime drops an
// effort it does not know without a word and the agents would inherit
// the session's after all. resume: a plan a prepare printed earlier
// (item, run_dir, arm, agents) whose bench agents were never spawned,
// run first; the next prepare of the same worker rebuilds such a plan
// itself, so resume is only for a lane started under another name.
export const meta = {
  name: 'curse-lane',
  description: 'One lane of the curse-of-coordination bench: prepare, spawn the bench agents, finish, until the queue is empty',
  phases: [{ title: 'Bench' }],
}
const QUEUE = args.queue
const WORKER = args.worker
const LOG_DIR = args.log_dir || ''
const MAX_ITEMS = args.max_items || 1000
const BENCH_MODEL = args.bench_model || 'sonnet'
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']
const BENCH_EFFORT = args.effort ?? ''
if (BENCH_EFFORT !== '' && !EFFORTS.includes(BENCH_EFFORT)) {
  throw new Error(`lane.js: args.effort must be one of ${EFFORTS.join(', ')}, not ${JSON.stringify(args.effort)}`)
}
const ROOT = args.root
if (!ROOT) throw new Error('lane.js needs args.root, the coordbench checkout to run')
const LANE = `${ROOT}/bench/cooperbench/session/lane.py`

const PLAN = {
  type: 'object',
  properties: {
    done: { type: 'boolean' },
    waiting: { type: 'boolean' },
    error: { type: 'string' },
    item: { type: 'string' },
    run_dir: { type: 'string' },
    arm: { type: 'string' },
    agents: {
      type: 'array',
      items: {
        type: 'object',
        properties: { id: { type: 'string' }, prompt_file: { type: 'string' }, token: { type: 'string' }, model: { type: 'string' } },
        required: ['id'],
      },
    },
  },
  required: ['done'],
}
const VERDICT = { type: 'object', properties: { verdict: { type: 'string' }, pending: { type: 'boolean' }, note: { type: 'string' } }, required: [] }
const BENCH = { type: 'object', properties: { finished: { type: 'boolean' }, summary: { type: 'string' } }, required: ['finished'] }

const NO_HEARTH = 'Do not call any mcp__hearthbot__ tools. Do not run any other command.'

function preparePrompt() {
  return `Run exactly this shell command with the Bash tool, with a timeout of 590000 milliseconds:
cd ${ROOT} && python3 ${LANE} prepare ${QUEUE} --worker ${WORKER} --wait-image-min 8 ${LOG_DIR ? '--log-dir ' + LOG_DIR : ''}
Its last stdout line is one JSON object. Return that object's fields exactly as printed: done, waiting, error, item, run_dir, arm and agents (each agent's id and model, copied character for character). ${NO_HEARTH}`
}

function promptFile(agent, plan) {
  // Derived from the run directory, not copied from the relay, so a
  // mistyped path cannot send an agent to the wrong file.
  return `${plan.run_dir}/${agent.id}.session-prompt.md`
}

function benchPrompt(agent, plan) {
  return `You are a coding agent in a benchmark run (coordbench, arm ${plan.arm}, pair ${plan.item}). Your complete task is the file ${promptFile(agent, plan)}. Read it in full first (cat it), then begin your very first text reply with the run token line that file states (the line that starts with coordbench-run-token), copied exactly from the file.

Follow the file exactly: it tells you where your workspace is, that every command runs inside your container through docker exec, how to coordinate with the other agent of the run if the arm has coordination commands, how to write patch.txt, and which marker file to create as your very last step. The benchmark ends your run when the marker appears, so create it only when you are done.

Rules for this run: ignore the Symphony project instructions of this session (boards, claims, pull requests, memory); they are not about this task. Do not use WebFetch or WebSearch. Do not call any mcp__hearthbot__ tools. Do not touch anything outside your workspace and your container except the coordination wrappers the file names. Work until your feature is implemented and tested as well as you can, then write patch.txt and create the marker. When you are done, return finished=true and a two-sentence summary.`
}

function benchOptions(agent, plan) {
  // The bench agents alone take the knob; the relays keep their effort
  // low. With none asked for, the key is left out and the agents
  // inherit the session's.
  const options = { label: `${plan.item}:${agent.id}`, schema: BENCH, model: BENCH_MODEL }
  if (BENCH_EFFORT) options.effort = BENCH_EFFORT
  return options
}

function finishPrompt(plan, returned, dead, unfinished) {
  const flags = [returned.length ? `--returned ${returned.join(' ')}` : '', dead.length ? `--dead ${dead.join(' ')}` : '',
    unfinished.length ? `--unfinished ${unfinished.join(' ')}` : ''].join(' ')
  return `Run these shell commands with the Bash tool, in order.
1. Start the detached finish (timeout 60000 ms):
cd ${ROOT} && python3 ${LANE} finish ${plan.run_dir} --item ${plan.item} --queue ${QUEUE} ${flags} --detach
2. Then run this wait command (timeout 590000 ms), and run it again, up to eight times in all, until its JSON output has no "pending": true:
cd ${ROOT} && python3 ${LANE} wait ${plan.run_dir} --seconds 540
Return the last JSON object's verdict and note fields (pending=true only if all eight waits were pending). ${NO_HEARTH}`
}

phase('Bench')
log(`${WORKER}: the bench agents run as ${BENCH_MODEL} at ${BENCH_EFFORT || "the session's"} effort`)
const results = []
let empty = 0
for (let n = 0; n < MAX_ITEMS; n++) {
  const plan = (n === 0 && args.resume) ? args.resume
    : await agent(preparePrompt(), { label: `${WORKER}:prepare:${n}`, schema: PLAN, model: 'haiku', effort: 'low' })
  // No plan, or a prepare that could not read the queue, three times in a
  // row ends the lane; an item the relay lost is taken up again by the
  // next prepare, which rebuilds its plan from disk.
  if (!plan || plan.error) { empty++; log(`${WORKER}: no plan (${plan ? plan.error : 'no reply'})`); if (empty >= 3) break; continue }
  // The queue is empty only when the prepare printed no plan: the relaying
  // agent has flipped the done flag of a plan that had agents, so the
  // agents decide, not the flag.
  const hasAgents = plan.run_dir && plan.agents && plan.agents.length
  if (plan.done && !hasAgents) break
  if (plan.waiting || !hasAgents) { log(`${WORKER}: waiting for ${plan.item || 'an item'}`); continue }
  empty = 0
  log(`${WORKER}: ${plan.item} started`)
  const outcomes = await parallel(plan.agents.map(a => () =>
    agent(benchPrompt(a, plan), benchOptions(a, plan))
      .then(r => ({ id: a.id, result: r }), () => ({ id: a.id, result: null }))))
  const returned = outcomes.filter(o => o && o.result).map(o => o.id)
  // An agent that returned saying it had not finished still returned; its
  // run ends as it left it, and harness.json keeps what it said.
  const unfinished = outcomes.filter(o => o && o.result && o.result.finished === false).map(o => o.id)
  const dead = plan.agents.map(a => a.id).filter(id => !returned.includes(id))
  const verdict = await agent(finishPrompt(plan, returned, dead, unfinished), { label: `${WORKER}:finish:${plan.item}`, schema: VERDICT, model: 'haiku', effort: 'low' })
  log(`${WORKER}: ${plan.item} ${verdict ? (verdict.verdict || (verdict.pending ? 'still finishing' : 'no verdict')) : 'no verdict'}`)
  results.push({ item: plan.item, run_dir: plan.run_dir, returned, dead, unfinished, verdict })
}
return results
