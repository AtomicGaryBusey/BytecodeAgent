## Run eval/runs/hex-astra-20261001-165154

### Subjects and variants

| subject | model reported | variant | planned | cells | pass@1 | pass final | mean attempts | calls | infra errors | effort | CLI | cost (list) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| codex_gpt-6-astra | gpt-6-astra (requested; not reported) | baseline | 16 | 16 | 15/16 (94%) | 16/16 (100%) | 1.06 | 18 | 1 | cli-default (low) | codex 0.157.1 | n/a |

calls = scored attempts + infrastructure errors (retried calls that are not attempts); planned = cells the run was asked for (plan.json), cells = cells completed.

### By subject × path × ISA (baseline variant)

| subject | path | ISA | cells | pass@1 | pass final | mean attempts |
|---|---|---|---|---|---|---|
| codex_gpt-6-astra | hex | x86_64 | 8 | 7/8 (88%) | 8/8 (100%) | 1.12 |
| codex_gpt-6-astra | hex | aarch64 | 8 | 8/8 (100%) | 8/8 (100%) | 1.00 |

### By subject × battery (baseline variant)

| subject | battery | cells | pass@1 | pass final | mean attempts |
|---|---|---|---|---|---|
| codex_gpt-6-astra | round1 | 16 | 15/16 (94%) | 16/16 (100%) | 1.06 |

### By subject × variant

| subject | variant | cells | pass@1 | pass final | mean attempts |
|---|---|---|---|---|---|
| codex_gpt-6-astra | baseline | 16 | 15/16 (94%) | 16/16 (100%) | 1.06 |

### Failed attempts by category and subject (baseline variant)

| category | codex_gpt-6-astra | total |
|---|---|---|
| crash | 1 | 1 |

First-attempt failures, codex_gpt-6-astra (baseline): crash 1

cells: 16; pass@1 15/16 (94%); pass final 16/16 (100%); attempts 17 (all subjects and variants pooled)

## codex_gpt-6-astra/baseline

#### Per cell (pass@1 / pass within 4 / attempts used)

| task | stream x86_64 | stream aarch64 | structured x86_64 | structured aarch64 | ir x86_64 | ir aarch64 | hex x86_64 | hex aarch64 |
|---|---|---|---|---|---|---|---|---|
| sum_to | not run | not run | not run | not run | not run | not run | 0 / ✓ / 2 | 1 / ✓ / 1 |
| gcd | not run | not run | not run | not run | not run | not run | 1 / ✓ / 1 | 1 / ✓ / 1 |
| popcount | not run | not run | not run | not run | not run | not run | 1 / ✓ / 1 | 1 / ✓ / 1 |
| fib | not run | not run | not run | not run | not run | not run | 1 / ✓ / 1 | 1 / ✓ / 1 |
| bmax | not run | not run | not run | not run | not run | not run | 1 / ✓ / 1 | 1 / ✓ / 1 |
| reverse | not run | not run | not run | not run | not run | not run | 1 / ✓ / 1 | 1 / ✓ / 1 |
| bubble_sort | not run | not run | not run | not run | not run | not run | 1 / ✓ / 1 | 1 / ✓ / 1 |
| count_nonzero_prefix | not run | not run | not run | not run | not run | not run | 1 / ✓ / 1 | 1 / ✓ / 1 |

#### By path

| path | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| hex | 16 | 15/16 (94%) | 16/16 (100%) | 1.06 |

#### By ISA

| ISA | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| x86_64 | 8 | 7/8 (88%) | 8/8 (100%) | 1.12 |
| aarch64 | 8 | 8/8 (100%) | 8/8 (100%) | 1.00 |

#### By path × ISA

| path × ISA | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| hex x86_64 | 8 | 7/8 (88%) | 8/8 (100%) | 1.12 |
| hex aarch64 | 8 | 8/8 (100%) | 8/8 (100%) | 1.00 |

#### By task

| task | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| sum_to | 2 | 1/2 (50%) | 2/2 (100%) | 1.50 |
| gcd | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 |
| popcount | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 |
| fib | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 |
| bmax | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 |
| reverse | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 |
| bubble_sort | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 |
| count_nonzero_prefix | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 |

#### Overall

cells: 16; pass@1 15/16 (94%); pass@≤4 16/16 (100%); attempts (calls) 17; infrastructure errors (retried calls, not attempts) 1

#### Failed attempts by category and path

| category | stream | structured | ir | hex | total |
|---|---|---|---|---|---|
| crash | 0 | 0 | 0 | 1 | 1 |

First-attempt failure categories: crash 1

#### Latency and size (per attempt)

| path | ISA | median wall s | median first-text s | median first-insn s (stream) | median check s | median final code bytes |
|---|---|---|---|---|---|---|
| hex | x86_64 | 10.6 | 10.6 | - | 0.03 | 26.5 |
| hex | aarch64 | 13.7 | 13.7 | - | 0.05 | 38 |

Models reported by the stream: {'gpt-6-astra (requested; not reported)': 17}; reported cost (list price) n/a

