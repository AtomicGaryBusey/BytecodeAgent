Muse Spark 1.3 cells superseded on 2026-11-11 and re-run from scratch. Muse Code's stream guard discards a streamed
sample that contains characters of another script ('stream guard resample (foreign-script)') and samples again within
the same step; the first adapter kept the discarded sample's deltas, so the graded answer was the partial first sample
followed by the whole second one (a duplicate label, scored as an assembler failure), and it flagged the second model
task as an isolation error. The adapter now grades Muse's final answer (run.terminal.completed) and records the
resample. Every cell with a resample in any attempt was moved here, attempts, results and infra records included.
