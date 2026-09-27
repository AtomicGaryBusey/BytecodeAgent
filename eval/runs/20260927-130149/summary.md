### Per cell (pass@1 / pass within 4 / attempts used)

| task | stream x86_64 | stream aarch64 | structured x86_64 | structured aarch64 | ir x86_64 | ir aarch64 |
|---|---|---|---|---|---|---|
| sum_to | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| gcd | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 0 / ✓ / 2 | 1 / ✓ / 1 |
| popcount | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 0 / ✓ / 2 |
| fib | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| bmax | 1 / ✓ / 1 | 0 / ✓ / 3 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| reverse | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| bubble_sort | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| count_nonzero_prefix | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |

### By path

| path | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| stream | 16 | 15/16 (94%) | 16/16 (100%) | 1.12 |
| structured | 16 | 16/16 (100%) | 16/16 (100%) | 1.00 |
| ir | 16 | 14/16 (88%) | 16/16 (100%) | 1.12 |

### By ISA

| ISA | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| x86_64 | 24 | 23/24 (96%) | 24/24 (100%) | 1.04 |
| aarch64 | 24 | 22/24 (92%) | 24/24 (100%) | 1.12 |

### By path × ISA

| path × ISA | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| stream x86_64 | 8 | 8/8 (100%) | 8/8 (100%) | 1.00 |
| stream aarch64 | 8 | 7/8 (88%) | 8/8 (100%) | 1.25 |
| structured x86_64 | 8 | 8/8 (100%) | 8/8 (100%) | 1.00 |
| structured aarch64 | 8 | 8/8 (100%) | 8/8 (100%) | 1.00 |
| ir x86_64 | 8 | 7/8 (88%) | 8/8 (100%) | 1.12 |
| ir aarch64 | 8 | 7/8 (88%) | 8/8 (100%) | 1.12 |

### By task

| task | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| sum_to | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| gcd | 6 | 5/6 (83%) | 6/6 (100%) | 1.17 |
| popcount | 6 | 5/6 (83%) | 6/6 (100%) | 1.17 |
| fib | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| bmax | 6 | 5/6 (83%) | 6/6 (100%) | 1.33 |
| reverse | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| bubble_sort | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| count_nonzero_prefix | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |

### Overall

cells: 48; pass@1 45/48 (94%); pass@≤4 48/48 (100%); attempts (calls) 52

### Failed attempts by category and path

| category | stream | structured | ir | total |
|---|---|---|---|---|
| compile | 0 | 0 | 2 | 2 |
| crash | 1 | 0 | 0 | 1 |
| wrong_result | 1 | 0 | 0 | 1 |

First-attempt failure categories: compile 2, crash 1

### Latency and size (per attempt)

| path | ISA | median wall s | median first-text s | median first-insn s (stream) | median check s | median final code bytes |
|---|---|---|---|---|---|---|
| stream | x86_64 | 4.3 | 2.3 | 2.4 | 0.04 | 34 |
| stream | aarch64 | 4.5 | 2.5 | 2.7 | 0.08 | 42 |
| structured | x86_64 | 5.7 | 2.6 | - | 0.06 | 30.5 |
| structured | aarch64 | 5.9 | 3.3 | - | 0.10 | 40 |
| ir | x86_64 | 5.4 | 2.9 | - | 0.08 | 48.5 |
| ir | aarch64 | 4.8 | 2.7 | - | 0.07 | 78 |

Models reported by the stream: {'claude-opus-5-5': 52}; reported cost (list price) $1.56

