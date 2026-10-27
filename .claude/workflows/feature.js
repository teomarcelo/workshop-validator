export const meta = {
  name: 'feature',
  description: 'Producer task loop: build from a brief in a worktree, test, review, fix, re-review',
  whenToUse: 'One brief from ~/.workshop-validator/team/briefs/. args: {brief, branch, role}',
  phases: [
    { title: 'Build', detail: 'executor in its own worktree, commits on the brief branch' },
    { title: 'Test', detail: 'test-runner on the branch' },
    { title: 'Review', detail: 'code-reviewer and rule-auditor, fresh agents' },
    { title: 'Fix', detail: 'at most 2 rounds, then escalate' },
  ],
}

const REPORT = {
  type: 'object',
  properties: {
    branch: { type: 'string' },
    commits: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
    acceptance: { type: 'array', items: { type: 'string' } },
    tokens_estimate: { type: 'number' },
  },
  required: ['branch', 'summary', 'acceptance'],
}
const VERDICT = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['APPROVE', 'VETO'] },
    findings: { type: 'array', items: { type: 'string' } },
  },
  required: ['verdict', 'findings'],
}
const TESTS = {
  type: 'object',
  properties: { green: { type: 'boolean' }, failures: { type: 'array', items: { type: 'string' } } },
  required: ['green', 'failures'],
}

const { brief, branch, role } = args
const build = (extra, model) => agent(
  `You are the ${role}. Read .claude/agents/${role}.md first and follow it. Your only instructions are the brief at ${brief}. ` +
  `Work on branch ${branch} (create it from origin/main if it does not exist). Commit on that branch; never push or merge. ${extra}`,
  { phase: 'Build', label: `build:${role}`, isolation: 'worktree', schema: REPORT, agentType: 'general-purpose', model })

const test = () => agent(
  `Check out branch ${branch} in a temporary worktree and run the unit tests, compile check and public_safety. Return failures only.`,
  { phase: 'Test', label: 'test-runner', agentType: 'test-runner', schema: TESTS })

const REVIEWERS = ['code-reviewer', 'rule-auditor']
const review = () => parallel(REVIEWERS.map(r => () =>
  agent(`Review \`git diff origin/main...${branch}\` against the brief at ${brief}. You did not write it.`,
    { phase: 'Review', label: r, agentType: r === 'code-reviewer' ? 'general-purpose' : r, schema: VERDICT })
    .then(v => v && { role: r, ...v })))

// BEGIN collectFindings (pure; tests/test_team_kit.py runs it with node)
// A missing reviewer is a finding, and "not ready" never yields an empty list.
function collectFindings(tests, results, roles) {
  const findings = []
  if (!tests) findings.push('test-runner returned nothing')
  else if (!tests.green) {
    const failures = (tests.failures || []).map(f => `test: ${f}`)
    findings.push(...(failures.length ? failures : ['test: not green, no failures listed']))
  }
  const verdicts = []
  roles.forEach((role, i) => {
    const v = results && results[i]
    if (!v || !v.verdict) { findings.push(`missing reviewer: ${role}`); return }
    verdicts.push({ ...v, role })
    if (v.verdict !== 'APPROVE') {
      const list = (v.findings || []).map(f => `${role}: ${f}`)
      findings.push(...(list.length ? list : [`${role}: ${v.verdict} with no findings listed`]))
    }
  })
  return { verdicts, findings, ready: findings.length === 0 }
}
// END collectFindings

let report = await build('')
let rounds = 0
while (true) {
  const tests = await test()
  const { verdicts, findings, ready } = collectFindings(tests, await review(), REVIEWERS)
  if (ready) return { report, verdicts, rounds, status: 'READY_FOR_PR' }
  if (rounds >= 2) return { report, verdicts, rounds, findings, status: 'ESCALATE' }
  rounds += 1
  log(`Fix round ${rounds}: ${findings.length} finding(s)`)
  report = await build(`This is fix round ${rounds}. Fix exactly these findings, nothing else:\n- ${findings.join('\n- ')}`,
    rounds === 2 ? 'opus' : undefined)
}
