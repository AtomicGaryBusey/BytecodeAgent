### Per cell (pass@1 / pass within 4 / attempts used)

| task | stream x86_64 | stream aarch64 | structured x86_64 | structured aarch64 | ir x86_64 | ir aarch64 |
|---|---|---|---|---|---|---|
| fib_rec | 1 / ✓ / 1 | 1 / ✓ / 1 | not run | not run | not run | not run |
| map_sum | 1 / ✓ / 1 | 1 / ✓ / 1 | not run | not run | not run | not run |
| dot_i32_simd | 1 / ✓ / 1 | 1 / ✓ / 1 | N/A | N/A | N/A | N/A |
| crc32 | not run | not run | 1 / ✓ / 1 | not run | not run | not run |
| isqrt | not run | not run | not run | not run | not run | 1 / ✓ / 1 |

### By path

| path | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| stream | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| structured | 1 | 1/1 (100%) | 1/1 (100%) | 1.00 |
| ir | 1 | 1/1 (100%) | 1/1 (100%) | 1.00 |

### By ISA

| ISA | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| x86_64 | 4 | 4/4 (100%) | 4/4 (100%) | 1.00 |
| aarch64 | 4 | 4/4 (100%) | 4/4 (100%) | 1.00 |

### By path × ISA

| path × ISA | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| stream x86_64 | 3 | 3/3 (100%) | 3/3 (100%) | 1.00 |
| stream aarch64 | 3 | 3/3 (100%) | 3/3 (100%) | 1.00 |
| structured x86_64 | 1 | 1/1 (100%) | 1/1 (100%) | 1.00 |
| ir aarch64 | 1 | 1/1 (100%) | 1/1 (100%) | 1.00 |

### By task

| task | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| fib_rec | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 |
| map_sum | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 |
| dot_i32_simd | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 |
| crc32 | 1 | 1/1 (100%) | 1/1 (100%) | 1.00 |
| isqrt | 1 | 1/1 (100%) | 1/1 (100%) | 1.00 |

### Overall

cells: 8; pass@1 8/8 (100%); pass@≤4 8/8 (100%); attempts (calls) 8

### Failed attempts by category and path

| category | stream | structured | ir | total |
|---|---|---|---|---|

First-attempt failure categories: 

### Latency and size (per attempt)

| path | ISA | median wall s | median first-text s | median first-insn s (stream) | median check s | median final code bytes |
|---|---|---|---|---|---|---|
| stream | x86_64 | 5.2 | 3.6 | 3.6 | 1.51 | 61 |
| stream | aarch64 | 5.7 | 3.3 | 3.4 | 0.51 | 76 |
| structured | x86_64 | 7.5 | 4.3 | - | 0.03 | 58 |
| ir | aarch64 | 8.6 | 5.2 | - | 0.11 | 100 |

Models reported by the stream: {'claude-opus-5-5': 8}; reported cost (list price) $0.21

