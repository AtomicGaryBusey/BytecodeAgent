## Run eval/runs/apps-hex-astra-20261001-174623

### Subjects and variants

| subject | model reported | variant | planned | cells | pass@1 | pass final | mean attempts | calls | infra errors | effort | CLI | cost (list) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| codex_gpt-6-astra | gpt-6-astra (requested; not reported) | baseline | 16 | 16 | 15/16 (94%) | 16/16 (100%) | 1.06 | 17 | 0 | cli-default (low) | codex 0.157.1 | n/a |

calls = scored attempts + infrastructure errors (retried calls that are not attempts); planned = cells the run was asked for (plan.json), cells = cells completed.

### By subject × path × ISA (baseline variant)

| subject | path | ISA | cells | pass@1 | pass final | mean attempts |
|---|---|---|---|---|---|---|
| codex_gpt-6-astra | hex | x86_64 | 8 | 8/8 (100%) | 8/8 (100%) | 1.00 |
| codex_gpt-6-astra | hex | aarch64 | 8 | 7/8 (88%) | 8/8 (100%) | 1.12 |

### By subject × battery (baseline variant)

| subject | battery | cells | pass@1 | pass final | mean attempts |
|---|---|---|---|---|---|
| codex_gpt-6-astra | apps | 16 | 15/16 (94%) | 16/16 (100%) | 1.06 |

### App battery by subject (baseline variant)

| subject | cells | samples | pass@1 (mean over samples) | pass@1 | pass within 4 (mean over samples) | pass within 4, any sample | mean attempts | mean attempts to pass |
|---|---|---|---|---|---|---|---|---|
| codex_gpt-6-astra | 16 | 16 | 15/16 (94%) | 15/16 (94%) | 16/16 (100%) | 16/16 (100%) | 1.06 | 1.06 |

### App battery by subject × path × ISA (baseline variant)

| subject | path | ISA | cells | samples | pass@1 (mean over samples) | pass@1 | pass within 4 (mean over samples) | pass within 4, any sample | mean attempts | mean attempts to pass |
|---|---|---|---|---|---|---|---|---|---|---|
| codex_gpt-6-astra | hex | x86_64 | 8 | 8 | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 1.00 | 1.00 |
| codex_gpt-6-astra | hex | aarch64 | 8 | 8 | 7/8 (88%) | 7/8 (88%) | 8/8 (100%) | 8/8 (100%) | 1.12 | 1.12 |

### By subject × variant

| subject | variant | cells | pass@1 | pass final | mean attempts |
|---|---|---|---|---|---|
| codex_gpt-6-astra | baseline | 16 | 15/16 (94%) | 16/16 (100%) | 1.06 |

### Failed attempts by category and subject (baseline variant)

| category | codex_gpt-6-astra | total |
|---|---|---|
| no_exit | 1 | 1 |

First-attempt failures, codex_gpt-6-astra (baseline): no_exit 1

cells: 16; pass@1 15/16 (94%); pass final 16/16 (100%); attempts 17 (all subjects and variants pooled)

## codex_gpt-6-astra/baseline

#### App battery: per cell (attempt-1 passes over samples / pass@1 / passes within 4 over samples / mean attempts)

| task | hex x86_64 | hex aarch64 |
|---|---|---|
| rev_lines | 1/1 / ✓ / 1/1 / 1.00 | 1/1 / ✓ / 1/1 / 1.00 |
| wc | 1/1 / ✓ / 1/1 / 1.00 | 1/1 / ✓ / 1/1 / 1.00 |
| hexdump | 1/1 / ✓ / 1/1 / 1.00 | 1/1 / ✓ / 1/1 / 1.00 |
| sort_lines | 1/1 / ✓ / 1/1 / 1.00 | 1/1 / ✓ / 1/1 / 1.00 |
| base64 | 1/1 / ✓ / 1/1 / 1.00 | 1/1 / ✓ / 1/1 / 1.00 |
| rpn | 1/1 / ✓ / 1/1 / 1.00 | 1/1 / ✓ / 1/1 / 1.00 |
| bf | 1/1 / ✓ / 1/1 / 1.00 | 1/1 / ✓ / 1/1 / 1.00 |
| json_lint | 1/1 / ✓ / 1/1 / 1.00 | 0/1 / ✗ / 1/1 / 2.00 |

#### App battery by path

| path | cells | samples | pass@1 (mean over samples) | pass@1 | pass within 4 (mean over samples) | pass within 4, any sample | mean attempts | mean attempts to pass |
|---|---|---|---|---|---|---|---|---|
| hex | 16 | 16 | 15/16 (94%) | 15/16 (94%) | 16/16 (100%) | 16/16 (100%) | 1.06 | 1.06 |

#### App battery by ISA

| ISA | cells | samples | pass@1 (mean over samples) | pass@1 | pass within 4 (mean over samples) | pass within 4, any sample | mean attempts | mean attempts to pass |
|---|---|---|---|---|---|---|---|---|
| x86_64 | 8 | 8 | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 1.00 | 1.00 |
| aarch64 | 8 | 8 | 7/8 (88%) | 7/8 (88%) | 8/8 (100%) | 8/8 (100%) | 1.12 | 1.12 |

#### App battery by path × ISA

| path × ISA | cells | samples | pass@1 (mean over samples) | pass@1 | pass within 4 (mean over samples) | pass within 4, any sample | mean attempts | mean attempts to pass |
|---|---|---|---|---|---|---|---|---|
| hex x86_64 | 8 | 8 | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) | 1.00 | 1.00 |
| hex aarch64 | 8 | 8 | 7/8 (88%) | 7/8 (88%) | 8/8 (100%) | 8/8 (100%) | 1.12 | 1.12 |

#### App battery by task

| task | cells | samples | pass@1 (mean over samples) | pass@1 | pass within 4 (mean over samples) | pass within 4, any sample | mean attempts | mean attempts to pass |
|---|---|---|---|---|---|---|---|---|
| rev_lines | 2 | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.00 | 1.00 |
| wc | 2 | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.00 | 1.00 |
| hexdump | 2 | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.00 | 1.00 |
| sort_lines | 2 | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.00 | 1.00 |
| base64 | 2 | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.00 | 1.00 |
| rpn | 2 | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.00 | 1.00 |
| bf | 2 | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 1.00 | 1.00 |
| json_lint | 2 | 2 | 1/2 (50%) | 1/2 (50%) | 2/2 (100%) | 2/2 (100%) | 1.50 | 1.50 |

#### App battery overall

cells: 16 (task × path × ISA), samples: 16; pass@1 15/16 (94%) (mean over samples); pass@1 15/16 (94%); pass within 4 16/16 (100%) (mean over samples), 16/16 (100%) with any sample; mean attempts 1.06; attempts (calls) 17

#### App battery: failed attempts by category and path

| category | hex | total |
|---|---|---|
| no_exit | 1 | 1 |

First-attempt failure categories: no_exit 1

#### App battery: latency and size (per attempt)

| path | ISA | median wall s | median first-text s | median response chars | median check s | median final code bytes |
|---|---|---|---|---|---|---|
| hex | x86_64 | 58.0 | 58.0 | 870.5 | 0.86 | 256 |
| hex | aarch64 | 49.4 | 49.3 | 887 | 0.59 | 370 |

Models reported by the stream: {'gpt-6-astra (requested; not reported)': 17}; reported cost (list price) n/a

