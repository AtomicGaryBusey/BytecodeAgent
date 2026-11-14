# Muse Code 1.4.20 re-audit (2026-11-13)

**How it arrived:** the launcher updated itself 1.4.18 -> 1.4.20 at 12:00:43, the moment a resumed step started. The
adapter's `muse --version` probe ran without `MUSE_NO_AUTO_UPDATE=1` (only model calls carried it). Fixed: CliSubject
gained `version_env`, Muse sets it; the test fake now refuses a version probe without the setting (7 of 8 Muse tests
fail without the fix). The adapter refused 1.4.20 (not audited) and both runs stopped; nothing was scored on it.

**Recorded request** (one call, muse-wonder-full-1.0-contributor, through eval/audits/recorder.py; raw/ gitignored):
same keys, effort `max` (summary auto), `max_output_tokens` 128000, no `tools`, our system prompt as the instructions'
tail, one model task. Muse's instructions: 63,961 -> 64,015 characters. **The only change is the identity line**:
1.4.18 told every model "You are powered by Muse Spark, a large language model trained by Meta MSL ... identify
yourself as 'Muse Code powered by Meta Muse Spark'"; 1.4.20 names the requested model ("powered by
muse-wonder-full-1.0-contributor, a large language model in the Muse model family from Meta"). So Wonder Full's
function, hex and app cells ran on 1.4.18 with the Muse Spark identity line; worth one sentence in the report.
