### Per cell (pass@1 / pass within 4 / attempts used)

| task | stream x86_64 | stream aarch64 | structured x86_64 | structured aarch64 | ir x86_64 | ir aarch64 |
|---|---|---|---|---|---|---|
| fib_rec | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| map_sum | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| sum8 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 0 / ✓ / 2 | 1 / ✓ / 1 |
| dot_i32_simd | 1 / ✓ / 1 | 1 / ✓ / 1 | N/A | N/A | N/A | N/A |
| binary_search | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| memmove_i64 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 0 / ✓ / 2 |
| crc32 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| mulhi_u64 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| add_overflow | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| isqrt | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| collatz_max | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |

### By path

| path | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| stream | 22 | 22/22 (100%) | 22/22 (100%) | 1.00 |
| structured | 20 | 20/20 (100%) | 20/20 (100%) | 1.00 |
| ir | 20 | 18/20 (90%) | 20/20 (100%) | 1.10 |

### By ISA

| ISA | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| x86_64 | 31 | 30/31 (97%) | 31/31 (100%) | 1.03 |
| aarch64 | 31 | 30/31 (97%) | 31/31 (100%) | 1.03 |

### By path × ISA

| path × ISA | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| stream x86_64 | 11 | 11/11 (100%) | 11/11 (100%) | 1.00 |
| stream aarch64 | 11 | 11/11 (100%) | 11/11 (100%) | 1.00 |
| structured x86_64 | 10 | 10/10 (100%) | 10/10 (100%) | 1.00 |
| structured aarch64 | 10 | 10/10 (100%) | 10/10 (100%) | 1.00 |
| ir x86_64 | 10 | 9/10 (90%) | 10/10 (100%) | 1.10 |
| ir aarch64 | 10 | 9/10 (90%) | 10/10 (100%) | 1.10 |

### By task

| task | cells | pass@1 | pass@≤4 | mean attempts |
|---|---|---|---|---|
| fib_rec | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| map_sum | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| sum8 | 6 | 5/6 (83%) | 6/6 (100%) | 1.17 |
| dot_i32_simd | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 |
| binary_search | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| memmove_i64 | 6 | 5/6 (83%) | 6/6 (100%) | 1.17 |
| crc32 | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| mulhi_u64 | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| add_overflow | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| isqrt | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |
| collatz_max | 6 | 6/6 (100%) | 6/6 (100%) | 1.00 |

### Overall

cells: 62; pass@1 60/62 (97%); pass@≤4 62/62 (100%); attempts (calls) 64

### Failed attempts by category and path

| category | stream | structured | ir | total |
|---|---|---|---|---|
| compile | 0 | 0 | 2 | 2 |

First-attempt failure categories: compile 2

### Latency and size (per attempt)

| path | ISA | median wall s | median first-text s | median first-insn s (stream) | median check s | median final code bytes |
|---|---|---|---|---|---|---|
| stream | x86_64 | 4.6 | 2.6 | 2.7 | 0.11 | 61 |
| stream | aarch64 | 4.9 | 2.5 | 2.5 | 0.09 | 72 |
| structured | x86_64 | 7.3 | 3.5 | - | 0.09 | 53 |
| structured | aarch64 | 6.9 | 2.8 | - | 0.09 | 66 |
| ir | x86_64 | 5.8 | 2.3 | - | 0.15 | 81 |
| ir | aarch64 | 5.6 | 2.5 | - | 0.11 | 134 |

Models reported by the stream: {'claude-opus-5-5': 64}; reported cost (list price) $2.23

