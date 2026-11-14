# Live-model eval: agent-harness studies (Mistral Vibe, Meta Muse Code)

> **These rows are not comparable with any other table in this repository and are never ranked with them.** Every
> other row is tool-free: the model receives our system prompt, the task prompt, and nothing else. Two vendors'
> coding-agent CLIs cannot be configured that way, so their models are measured *inside the vendor's agent harness*
> and reported here, apart. Plans: Mistral Vibe on the user's Mistral Pro plan (a $300 monthly Vibe Code allowance, separate from the
> plan's API allowance, which these runs did not touch; $141 of it was used by 2026-11-12, this study included); Meta Muse Code on a Meta AI account
> with no subscription allowance, running the `-contributor` model in Meta's contributor tier (sessions are shared
> with Meta). Method and fairness contract for everything else:
> [cross-vendor-methodology.md](cross-vendor-methodology.md).

## Why these are separate

The harness's contract for a CLI subject is: zero tools, our system prompt as the only system text, a private home
per call, and each call verified against the CLI's own record of the request. Two CLIs fail the first two points
and no setting fixes it. Both were audited with a local recorder in front of the vendor's API
(`eval/audits/recorder.py`), which saved the request each CLI actually sent.

| | Mistral Vibe 3.1.2 | Meta Muse Code 1.4.15 / 1.4.18 |
|---|---|---|
| Tool definitions in the request | **7, always** (bash, read_file, write_file, edit, run_typescript, search_tool_functions, skill); no setting removes them | **None** (`--preset empty`: the request has no `tools` field) |
| Can a tool run? | No. `disabled_tools = ["*"]` denies every one and the headless `ask` agent cannot be approved; a denied attempt is counted, a tool that ran is an isolation failure | Nothing to run |
| System text | A ~6,800-character Vibe agent prompt with our system prompt embedded in it (`--system-prompt-file` replaces only an inner section) | Muse's own agent prompt, 60,598 characters on 1.4.15 and 63,961 on 1.4.18 (identity, tone, repository work, tool-use sections), with our system prompt appended as its tail; plus a 27,000-28,000-character developer message of workspace reminders |
| Effort | Catalog default, `high` for both models | CLI default, `max` (the CLI's help says `high`; the recorded request says `max`) |
| Turns | `--max-turns 4` (room to answer after a denied tool call) | One model step |
| Hidden resampling | None seen | **Yes**: a stream guard discards a sample containing characters of another script and samples again |
| Reported model | Not reported by the CLI (requested id recorded) | Reported, and checked on every call |

So these numbers answer "how does the model do inside its vendor's agent harness, with the tools switched off",
not the tool-free question. The task prompts, the scoring, the four attempts with repair feedback and the uniform
time limits (180 s, 400 s on the long battery, 900 s for apps) are the same as everywhere else.

## Results

From `eval/harness_tables.py`. Function batteries (224 cells):

| Model (harness) | Round 1 (48) | Round 2 (62) | Long (54) | Adversarial (60) | Pass@1 | Within 4 | Mean attempts |
|---|---|---|---|---|---|---|---|
| Meta Muse Spark 1.3 (Muse Code harness) | 48 | 62 | 52 | 60 | **222/224 (99%)** | 224/224 (100%) | 1.01 |
| Mistral Large 4 (Vibe harness) | 40 | 52 | 22 | 42 | **156/224 (70%)** | 219/224 (98%) | 1.44 |
| Mistral Medium 3.5 (Vibe harness) | 28 | 27 | 11 | 25 | **91/224 (41%)** | 154/224 (69%) | 2.38 |

By path, and the app and hex batteries:

| Model (harness) | Stream | Structured | IR (QBE) | Apps, first attempt (96) | Apps within 4 | Hex functions, first / within 4 (16) | Hex programs, first / within 4 (16) |
|---|---|---|---|---|---|---|---|
| Meta Muse Spark 1.3 (Muse Code harness) | 76/76 | 73/74 | 73/74 | 90/96 | 96/96 | 15/16 / 16/16 | 14/16 / 16/16 |
| Mistral Large 4 (Vibe harness) | 67/76 | 48/74 | 41/74 | 65/96 | 96/96 | 9/16 / 14/16 | 3/16 / 8/16 |
| Mistral Medium 3.5 (Vibe harness) | 48/76 | 38/74 | 5/74 | 19/96 | 52/96 | 2/16 / 6/16 | not run (gate: 12/16 hex functions within 4) |

What the harness added, from each CLI's own record of every scored attempt (all batteries):

| Model (harness) | Scored attempts | CLI versions | Timeouts | Median call | Median input / output tokens | Denied tool attempts | Harness lookups | Guard resamples | Infra retries (not scored) |
|---|---|---|---|---|---|---|---|---|---|
| Meta Muse Spark 1.3 (Muse Code harness) | 366 | muse 1.4.15 ×341, muse 1.4.18 ×25 | 9 | 85 s | 19,691 / 9,971 | none possible (no tools sent) | — | 66 in 66 attempts (18%) | 7 |
| Mistral Large 4 (Vibe harness) | 539 | vibe 3.1.2 ×539 | 92 | 77 s | 6,029 / 7,433 | 154 in 120 attempts | 307 | — | 0 |
| Mistral Medium 3.5 (Vibe harness) | 877 | vibe 3.1.2 ×877 | 34 | 78 s | 6,107 / 13,850 | 1 in 1 attempts | 0 | — | 0 |

Tools the Vibe models reached for (every attempt was denied; none ran):

| Model (harness) | Tool | Attempts naming it |
|---|---|---|
| Mistral Large 4 (Vibe harness) | `file_system.bash` | 109 |
| Mistral Large 4 (Vibe harness) | `file_system.write_file` | 21 |
| Mistral Large 4 (Vibe harness) | `file_system.read_file` | 1 |
| Mistral Medium 3.5 (Vibe harness) | `file_system.read_file` | 1 |

## Findings

- **Muse Spark 1.3 inside Muse Code: 222/224 first attempt (99%), every cell within four**, with 90/96 on the app
  battery, 15/16 hex functions and 14/16 hex programs on the first attempt. Read beside the tool-free tables, only
  Codex GPT-6 Astra (221) is in that range on functions, but **the comparison is not like for like** and should not
  be made as a ranking: Muse answers under a 60,000-character agent prompt, at `max` effort, with a hidden resample
  (below). What can be said is that the harness did not get in its way.
- **Muse Code resamples behind the caller's back, and far more on whole programs.** When a streamed answer contains
  characters of another script, Muse fails that sample and draws another inside the same step. It happened in 66 of
  366 scored attempts (18%): 8 of 227 function attempts (4%), none of 17 hex-function attempts, 4 of 19 hex-program
  attempts, and **54 of 103 app attempts (52%)**. The model's answer here is always the sample Muse kept. 58 of
  the 66 resampled attempts passed, against 294 of the 300 that were not resampled, so the guard is not rescuing
  failures; but each resample is a draw no tool-free row gets, which is one more reason these rows stand apart.
- **Mistral Large 4 inside Vibe: 156/224 (70%), 219 within four**, and 65/96 on apps with every cell recovered.
  **Its largest single loss is the harness, not the task**: 92 of its failed attempts are timeouts, and 82 of
  those are Vibe stopping the turn at the 4-turn limit without a final answer, the turns having gone on tools it
  could not use. It reached for a tool in 120 of 539 attempts (`bash` 109 times, `write_file` 21), every time
  denied. One answer says so in the first line of what should have been an assembly program: "bash execution is
  denied in this environment, so I'll write the assembly carefully by hand (no local assembler available to test
  against)". The long battery shows it most (22 of 54). It also made 307 `search_tool_functions` lookups, the
  harness's own tool-catalogue query, which returns a count and reaches nothing outside.
- **Mistral Medium 3.5 inside Vibe: 91/224 (41%), 154 within four**; QBE 5/74. It almost never reached for a tool
  (once), so its result is closer to what the model does unaided: 254 of its failed attempts are invalid QBE.
  19/96 on apps, 6/16 hex functions within four, so the hex-program gate was not met.
- **Whether the harness helps or hurts depends on the model's habits.** Large 4 loses turns to tool attempts a
  tool-free run would not offer; Medium 3.5 ignores the tools; Muse is sent none. A tool-free row for either
  Mistral model would need Mistral's API directly (pay-per-token, not run).

## Faults found and fixed during the studies

Both were adapter faults, found from the runs' own records, and the affected cells were moved to `superseded/` in
their run directories and re-run from scratch before anything was reported.

- **Vibe: the turn limit was retried as an infrastructure error**, which gave Mistral Large 4 extra draws; so was
  a completed `search_tool_functions` lookup. The turn limit is now a scored timeout and the lookup a recorded
  harness event. All 19 Large 4 cells with any infrastructure entry were superseded and re-run.
- **Muse: the discarded sample was graded.** The first adapter passed on Muse's streamed text as it arrived, so a
  guard resample produced the discarded partial answer followed by the new one (a duplicate label, scored as an
  assembler failure), and the second model task tripped the isolation check. The adapter now grades Muse's own
  final answer and checks it against the sample that completed. Three function cells were superseded and re-run.

Two things changed under the Muse study and are recorded per attempt: Muse Code updated itself from 1.4.15 to
1.4.18 when the machine was rebooted on 2026-11-12, after 341 of the 366 scored attempts. 1.4.18 was re-audited
before any cell ran on it (`eval/audits/muse-1.4.18-20261112`): the same request shape, effort and output cap;
Muse's own instructions grew by about 3,400 characters. The last 6 app cells and all 16 hex programs ran on 1.4.18.
Auto-update is now switched off for every call.

## What was not done

- GLM-5.3 and GLM-5.3 Flash, also served through Vibe, were not run (GLM-5.3 is measured tool-free on Fireworks).
- No tool-free Mistral or Meta rows exist. Mistral's API is pay-per-token and was not approved; Meta's API route
  was not investigated beyond noting that the Muse binary accepts an API key.
- The raw 1.4.15 Muse and 3.1.2 Vibe audit captures were lost with `/tmp` in the power loss of 2026-11-12; their
  findings are recorded in the adapters' docstrings (`bca/liveeval/vendor_muse.py`, `vendor_vibe.py`). Audits from
  then on are kept under `eval/audits/`.
