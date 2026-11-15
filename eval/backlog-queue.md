# Backlog queue — "proceed with the next loop"

Worked by an event-driven loop (2026-11-14; it was a 10-, then 30-minute poll). `eval/watch.py` runs in the
background and exits on the first event (a run stops or starts, a new abort line, a guard trip, a stall, low memory,
or a 6-hour heartbeat), which wakes the session; a 3-hourly cron job is the backup and re-arms the watcher. Tools:
`eval/status.py` (one report of everything live or parked, with real wake times), `eval/stop.py` (stop by PID:
never `pkill -f`), `eval/pin.py` + `eval/pins.json` (pinned CLI binaries: Muse 1.4.20, agy 1.5.7, Claude Code
2.1.323; an update needs an audit, then a re-pin), `eval/regenerate.py` (all tables, analytics, dashboard; refuses
to run while an unfinished run is not in `model_names.IN_PROGRESS`). Each wake:

1. **Is a backlog item actively working?** A live `bca.liveeval run` process or chain script for an item below that
   is making calls. If yes, let it run (check it is healthy: no abort, isolation error or crash; handle one if
   found). A run only *waiting* on a vendor quota reset (Argon, a Kimi window) is **parked**, not active: it does
   not block the next item.
2. **If none is active**, take the first item under *Eligible* whose prerequisites hold, start it (smoke first for a
   new subject), and move it to *In progress*.
3. **When an item finishes**: review it (first-attempt failures, false rejects, harness faults). Any finding that
   needs work becomes a new item at the end of *Eligible* (or *Needs the user*) with its source named. Then mark it
   done.
4. **Stop** when *Eligible* and *In progress* are both empty: present *Needs the user* in one message.

Standing rules apply to every item: uniform time limits and output caps; no pay-per-token spend without a cost
estimate and the user's approval; Codex/ChatGPT plan only for models the user names; never commit or push unless
asked; never spoof a vendor client; local models yield to the CodeGraph agent (guard: pause at MemAvailable <= 4 GB or
swap >= 8 GB; user, 2026-11-14, replacing MemFree <= 1 GB).

**Power loss 2026-11-12 03:52** (machine hung; journal ends 03:51 with no panic/OOM/lockup line; NVRM
NV_ERR_NO_MEMORY allocation failures 22:56-03:34). Rebooted 07:49 into kernel 7.0.0-1019 (was 6.17.0-1032). No record
was corrupted; calls in flight had no attempt record and re-ran. All chains moved to `eval/runs/chain-lib.sh`:
finished steps are skipped, logs appended, and the hex gate read from the cell records (the old chains read it from
a log that a resumed run rewrites, which would have skipped Opus 4.7's hex programs).

**Reboots 2026-11-12 12:32, 12:53, 15:23** (system updates; kernel 7.0.0-1025). No record corrupted. Chains relaunched
15:34 with the same resume-safe scripts. Claude Code updated itself 2.1.322 -> 2.1.323: every attempt records its
cli_version; attempts from 15:34 on are 2.1.323 (Opus 4.7's last 2 hex-program cells, then Sonnet 4.6, Opus 4.6, 4.5).

## In progress

- **Muse tier comparison resumed 07:1x** (`eval/runs/muse-tier-resume-20261113.sh`) after the search-proposal audit
  (eval/audits/muse-1.4.18-search-20261113: not reproduced in 3 replays). Fails closed if it recurs.
- **Muse round 2** (user, 2026-11-13, after subscribing to Muse Code Power Usage; contributor mode is fine for
  benchmarks): `eval/runs/muse-models-20261113.sh` runs muse-wonder-full-1.0, muse-wonder-1.0 and muse-ember-1.0 in the
  contributor tier, full set each (smokes 5/5). In parallel `eval/runs/muse-tier-20261113.sh` runs paid muse-spark-1.3
  on functions and apps, to compare with the contributor run of 11-11/12 (requests byte-identical apart from the model
  id: eval/audits/muse-1.4.18-tiers-20261113). When done: harness tables, a tier pair in eval/effort_tables.py, and the
  agent-harness report.
- **Claude output-cap re-run** (2026-11-12, approved by the user): Claude Code continued calls past `max_tokens`
  with an injected user turn; the Claude adapter now stops at the cap (scored as written) and fails closed on any
  other user turn. 46 cells with a continued attempt were superseded (`eval/supersede.py`; list in
  `eval/out/continued-cells.json`) and are re-run by `eval/runs/claude-cap-rerun-20261112.sh`: hex programs of Opus 4.8
  (1), Sonnet 5 (7), Mythos 5.1 (4), Opus 4.7 (5), then the older-Claude chain (Sonnet 4.6 functions 18, apps 11, ...).
  When done: regenerate and restate the four committed hex-program rows (Opus 4.8, Sonnet 5, Mythos 5.1, Opus 4.7)
  and Opus 4.7's row in the cross-vendor and hex reports. Other harnesses audited the same day: no continuation.
- **Copilot `kimi-k3`**: **parked until the credits reset on 2026-12-01.** Functions 214/224 first attempt (224
  within four; Kimi Code's K3: 204, p = 0.076); hex functions 12/16 (16 within four); hex programs 9 of 16 cells done
  (3 first attempt, 9 within four) when November's credits ran out at 21:30 on 11-12 (Copilot then fails every call:
  void attempts, nothing scored); apps not started. In December: `eval/runs/copilot-kimi-k3-rest-20261112.sh` resumes
  hex programs (7 cells), then apps; `gpt-5.3-codex` after that, once the user enables its policy.
- **Six older Claude models** (Claude Ultra plan): `eval/runs/claude-older-resume-20261112.sh` (was
  `claude-older-20261111.sh`), one model after another; Opus 4.7 hex programs, then Sonnet 4.6, Opus 4.6, Opus 4.5.
  **2026-11-13 03:5x: STOPPED by the user mid-apps (plan usage); see Needs the user.** Earlier: finish Sonnet 4.6, then pause; Opus 4.6 and Opus 4.5 are backlogged** (see Needs the user).
  The multi-model chain was stopped; `eval/runs/claude-sonnet-4-6-finish-20261113.sh` finishes Sonnet 4.6 alone
  (apps, then hex programs; waits 20 min and resumes after a Cloudflare 403 abort).
- **Kimi K3.1 Pro Highspeed, then K2.7 Code Highspeed** (Allegretto): queue `eval/runs/kimi-highspeed-20261111.sh`,
  resumer `eval/runs/kimi-highspeed-resume-20261111.sh`. **Parked on the 7-day cap since 11-12 01:53**: K3.1 Pro
  Highspeed functions and hex functions done, apps partway; K2.7 Code Highspeed not started. The resumer probes every
  15 minutes and restarts the queue when the week's window resets.
- **Gemini 4 Argon High functions** (Google AI Pro): own 8-hourly loop, restarted 11-12 after the user signed in
  again. agy updated itself to 1.5.7 at that sign-in: re-audited (eval/audits/agy-1.5.7-20261112, same request as
  1.5.3), Default agent mode pinned, and the sign-in token file (keyring unusable after the reboot) is now copied
  into each private home. Cells from here on are on agy 1.5.7 (recorded per attempt). Parked on quota until ~11-14.

## Eligible (in order)

1. **Regenerate the reports when a parked or running battery completes** (Sonnet 4.6, Opus 4.6, Opus 4.5; Kimi K3.1
   Pro Highspeed apps and hex programs; Kimi K2.7 Code Highspeed; Argon): remove the run from
   `eval/model_names.IN_PROGRESS`, review its first-attempt failures, then `eval/xv_tables.py`, `app_tables.py`,
   `hex_tables.py` > `eval/out/`, `eval/update_report_tables.py` for the cross-vendor, apps, hex reports and README,
   `eval/code_quality.py`, `eval/update_analytics_report.py`, `eval/dashboard.py`; add each model's notes by hand.
3. **Gemini 3.8 Flash High**: its last function cell (`stats_f.ir.aarch64`) and hex functions (16 cells), when the
   Google AI Pro quota allows (after Argon's window); hex programs at >= 12/16.
4. **(Grok half DONE 2026-11-13 10:08, grok 1.0.41: first attempt s1 -> s2 on the same 54 cells, Grok 4.7 build-fast 46 -> 52 (1/7 disagree, p 0.07), Grok 4.7 48 -> 44 (8/4, p 0.39), Grok 4.6 50 -> 47 (5/2, p 0.45); within four 54, 52, 54. Reported 2026-11-14 in live-model-eval-effort.md (both samples ran on grok 1.0.41, same effort). Claude half HELD, user stopped Claude-plan runs) Second long-battery sample, non-Codex members of the top seven**: Opus 5.5, Fable 5.1 (Claude plan); Grok 4.7
   build-fast, Grok 4.7, Grok 4.6 (SuperGrok). Whole battery, 54 cells each, ~270 calls.
5. **(HELD: Claude plan; needs the user's go-ahead since 2026-11-13) `declared` hex variants, two more samples for Opus 5.5** (~65 calls, Claude plan).
6. **(HELD: Claude plan; needs the user's go-ahead since 2026-11-13) `assisted` on Opus 4.8 and Sonnet 5**, rounds 1 + 2 (~280 calls, Claude plan).
7. **(DONE 2026-11-13 19:4x)** Kolibri-1 hex functions: 0/16 first and 0/16 within four (64 attempts: encoding 27, wrong result 20, token budget exhausted 11, hang 3, crash 2, oob 1; median 988 s and 23,196 tokens per call); gate not met, no hex programs. Served on vLLM's default MoE config (tuned one lost; speed only).
8. **(IN PROGRESS since 2026-11-13 23:0x; paused 11-14 00:25 by the old MemFree guard, restarted 00:4x with the new rule; from 11-14 ~02:1x 4 calls at once (BCA_VLLM_SLOTS) + n-gram speculative decoding (measured: per call 7.8 -> ~10.3 tok/s on task prompts, 4 at once ~41 tok/s total): `eval/runs/apriel-20261113.sh`, server eval/apriel/serve.sh, guard eval/apriel/mem-guard.sh; smoke 5/5, 4 first; ~7.8 tok/s, functions ~1.5 days) Apriel-1.6-15B-Thinker** local baseline: adapter holds back everything before `[BEGIN FINAL RESPONSE]` (vendor_vllm FINAL_MARKERS, tests added, uncommitted); weights pinned 5810d5f1.

## Needs the user (the loop does not start these)

- **Argon (Gemini 4 Argon High) STOPPED 2026-11-13 16:0x**: after Google's quota reset the run resumed on agy 1.5.7
  and the served backend changed: `gemini-4-argon-high` is now served as `gemini-4-argon-trial` (all 118 earlier
  answered attempts, agy 1.5.3: `gemini-4-argon-c`). 21 of the 24 new attempts were Gemini safety-filter blocks
  ("This request was blocked by Gemini's filters ...", returned as answer text, scored `format`); 0 of 180 before.
  The 9 cells touched on 1.5.7 were superseded (moved to superseded/, not scored). The loop
  (`agy-argon-20261107-142027.loop.sh`) is stopped. Run has 120/224 cells on 1.5.3. Decide: is the "-trial" route the
  same model (ask Google / check the agy catalog), and should the run continue on it, restart whole, or end at 120?
  Also: the agy adapter should score a Gemini filter block as a refusal, not `format` (adapter fix, small).

- **Sonnet 4.6 remainder, STOPPED by the user 2026-11-13 03:5x** ("It's using too much usage. I'm about to run out"):
  apps 80/96 (16 cells left; in-flight attempts were killed unrecorded, so they re-run from their last recorded
  attempt), hex programs (16, gate met) not started. Functions are complete (224/224). Resume only when the user
  says so: `eval/runs/claude-sonnet-4-6-finish-20261113.sh`. No Claude-plan run starts without the user's go-ahead.

- **Opus 4.6 and Opus 4.5** (`claude-opus-4-6`, `claude-opus-4-5-20251101`), full set each (~400-440 Claude-plan
  calls per model, about 12-15 h each): paused by the user on 2026-11-13, after Sonnet 4.6. Do not start until the
  user asks. When approved, run them with a Sonnet-style finisher (one model, 403-aware), not the old six-model chain.

- **Copilot, December**: `gpt-5.3-codex` (the user enables its policy in GitHub's Copilot settings first) and the
  rest of `kimi-k3`'s app battery need the credits that reset on 2026-12-01; November's are being spent on kimi-k3.
- **Qwen3.8-27B on the user's DGX cluster**: needs the endpoint, serving stack, model id, quantisation, sampling.
- **Pay-per-token**: MAI-Thinking-1 + DeepSeek-V4-Pro apps/hex (Foundry, ~$40-65); GLM-5.3 + Qwen 3.8 Max
  apps/hex (Fireworks, ~$120-190); further Foundry models; Mistral Studio API.
- **Codex plan**: Astra / Sol second long sample, Astra `declared_annotated` (the user names Codex models).
- **`misleading_hint` / `terse` on non-Claude models**: the user chooses the set.
- **Housekeeping**: Docker images (~72 GB), Forgejo GC.

## Done (this loop)

- Kimi K3.1 Pro Highspeed complete 2026-11-14 18:43 (apps 86/96 first, 96 within four; hex programs 4/16 first, 16 within four; by-hand check of each failure kind: all the model's own). Removed from IN_PROGRESS, tables regenerated, notes added.

- **Kolibri re-tune and commit** (user, 2026-11-13): config re-tuned (bs 1 on the GB10: M16 N32 K64 G1 w4 s5; 2048
  from B200 E=256 with num_stages 3) and kept in eval/kolibri-tune/; serve.sh verified (vLLM reads it from the repo);
  47.3 / 47.1 tok/s tuned vs 46-48 default, no gain on today's stack. All pending work committed in six commits
  (d24d37c8f .. f86d07c1e) plus the methodology note; NOT pushed (the user has not asked). `.git/gc.log` reports too
  many unreachable loose objects (left by the history rewrite): `git prune` is housekeeping for the user to approve.

- **Write-up of every finished run** (2026-11-12, at the user's request; not committed): by-hand review of the new
  runs (no false rejects; 162 suspect attempts in 39 groups, 15 cross-check rejections); cross-vendor, apps, hex,
  methodology, analytics, README and TODO updated; new reports `live-model-eval-agent-harness.md` (Vibe, Muse) and
  `live-model-eval-effort.md` (effort, host, serving tier); new generators and `eval/model_names.py`; list prices
  for the five Codex models and Kimi Highspeed; code quality rebuilt (12,624 passing solutions re-run, all passed
  again); analytics section 10 and the dashboard regenerated; dashboard artifact republished (version 8).
- Opus 4.7 (older-Claude chain, third of six), finished 16:04 on 11-12: functions 209/224 first (223 within four), hex functions 16/16 first (16 within four), apps 81/96 first (96 within four), hex programs 9/16 first (12 within four).
- Muse Spark 1.3 agent-harness study, finished 12:30 on 11-12: functions 222/224 first (224 within four), hex
  functions 15/16 (16), apps 90/96 (96), hex programs 14/16 (16). 371 attempts, 346 on Muse Code 1.4.15 and 25 on
  1.4.18; 66 guard resamples (18% of attempts: disclose, they are hidden extra samples); 7 infra retries; 9 timeouts.
- Vibe agent-harness study, finished 02:33 on 11-12: Large 4 fn 156/224, apps 65/96, hex fn 14/16, hex programs
  8/16 within four (3 first; several json_lint timeouts); Medium 3.5 fn 91/224, apps 19/96, hex fn 6/16 (gate not met).
- Fable 5 (older-Claude chain, second of six): functions, hex 16/16, apps, hex programs finished 01:45 on 11-12.
- Opus 5 (older-Claude chain, first of six): functions, hex, apps, hex programs finished 00:42 on 11-12.
