// One lane of a session-driven coordbench run, as a Claude Code Workflow
// script: take the next item of the shared queue, prepare it, spawn one
// subagent per bench agent, finish it, and go on until the queue is
// empty. Several lanes run at once as several workflows of the session
// (a workflow runs two agents at a time on a four-CPU host, a pair's
// two). Each lane's agents: a small one that runs lane.py prepare and
// relays its JSON, the bench agents (the pre-registered model), and a
// small one that starts the detached finish and waits for its verdict.
// args: {queue, worker, log_dir, max_items, bench_model, root, resume}
// resume: a plan a prepare printed earlier (item, run_dir, arm, agents)
// whose bench agents were never spawned, run first.
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
const ROOT = args.root || '/home/user/coordbench'
const LANE = `${ROOT}/bench/cooperbench/session/lane.py`

const PLAN = {
  type: 'object',
  properties: {
    done: { type: 'boolean' },
    waiting: { type: 'boolean' },
    item: { type: 'string' },
    run_dir: { type: 'string' },
    arm: { type: 'string' },
    agents: {
      type: 'array',
      items: {
        type: 'object',
        properties: { id: { type: 'string' }, prompt_file: { type: 'string' }, token: { type: 'string' }, model: { type: 'string' } },
        required: ['id', 'prompt_file', 'token'],
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
Its last stdout line is one JSON object. Return that object's fields exactly as printed: done, waiting, item, run_dir, arm and agents (each agent's id, prompt_file, token and model, copied character for character). ${NO_HEARTH}`
}

function benchPrompt(agent, plan) {
  return `You are a coding agent in a benchmark run (coordbench, arm ${plan.arm}, pair ${plan.item}). Your complete task is the file ${agent.prompt_file}. Read it in full first (cat it), then begin your very first text reply with the run token line that file states, exactly as written:
${agent.token}

Follow the file exactly: it tells you where your workspace is, that every command runs inside your container through docker exec, how to coordinate with the other agent of the run if the arm has coordination commands, how to write patch.txt, and which marker file to create as your very last step. The benchmark ends your run when the marker appears, so create it only when you are done.

Rules for this run: ignore the Symphony project instructions of this session (boards, claims, pull requests, memory); they are not about this task. Do not use WebFetch or WebSearch. Do not call any mcp__hearthbot__ tools. Do not touch anything outside your workspace and your container except the coordination wrappers the file names. Work until your feature is implemented and tested as well as you can, then write patch.txt and create the marker. When you are done, return finished=true and a two-sentence summary.`
}

function finishPrompt(plan, returned, dead) {
  const flags = [returned.length ? `--returned ${returned.join(' ')}` : '', dead.length ? `--dead ${dead.join(' ')}` : ''].join(' ')
  return `Run these shell commands with the Bash tool, in order.
1. Start the detached finish (timeout 60000 ms):
cd ${ROOT} && python3 ${LANE} finish ${plan.run_dir} --item ${plan.item} --queue ${QUEUE} ${flags} --detach
2. Then run this wait command (timeout 590000 ms), and run it again, up to eight times in all, until its JSON output has no "pending": true:
cd ${ROOT} && python3 ${LANE} wait ${plan.run_dir} --seconds 540
Return the last JSON object's verdict and note fields (pending=true only if all eight waits were pending). ${NO_HEARTH}`
}

phase('Bench')
const results = []
let empty = 0
for (let n = 0; n < MAX_ITEMS; n++) {
  const plan = (n === 0 && args.resume) ? args.resume
    : await agent(preparePrompt(), { label: `${WORKER}:prepare:${n}`, schema: PLAN, model: 'haiku', effort: 'low' })
  if (!plan) { empty++; if (empty >= 3) break; continue }
  // The queue is empty only when the prepare printed no plan: the relaying
  // agent has flipped the done flag of a plan that had agents, so the
  // agents decide, not the flag.
  const hasAgents = plan.run_dir && plan.agents && plan.agents.length
  if (plan.done && !hasAgents) break
  if (plan.waiting || !hasAgents) { log(`${WORKER}: waiting for ${plan.item || 'an item'}`); continue }
  empty = 0
  log(`${WORKER}: ${plan.item} started`)
  const outcomes = await parallel(plan.agents.map(a => () =>
    agent(benchPrompt(a, plan), { label: `${plan.item}:${a.id}`, schema: BENCH, model: BENCH_MODEL })
      .then(r => ({ id: a.id, result: r }), () => ({ id: a.id, result: null }))))
  const returned = outcomes.filter(o => o && o.result).map(o => o.id)
  const dead = plan.agents.map(a => a.id).filter(id => !returned.includes(id))
  const verdict = await agent(finishPrompt(plan, returned, dead), { label: `${WORKER}:finish:${plan.item}`, schema: VERDICT, model: 'haiku', effort: 'low' })
  log(`${WORKER}: ${plan.item} ${verdict ? verdict.verdict : 'no verdict'}`)
  results.push({ item: plan.item, run_dir: plan.run_dir, returned, dead, verdict })
}
return results
