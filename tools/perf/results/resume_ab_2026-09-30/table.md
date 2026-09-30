| arm, first 35 min (control) | completions | mean node/s | CPU ms/completion |
|---|---:|---:|---:|
| cont | 984820 | 469.0 | 46.47 |
| kill | 872254 | 447.6 | 46.44 |

| arm, second 35 min | completions | mean node/s | CPU ms/completion | best 15-min node/s | CPU ms/completion @best | cores @best | recompute share @best |
|---|---:|---:|---:|---:|---:|---:|---:|
| cont: uninterrupted | 956209 | 492.7 | 43.89 | 502.0 | 43.62 | 21.9 | 1.3% |
| kill: resumed after kill -9 | 923581 | 473.9 | 44.47 | 483.7 | 43.56 | 21.1 | 3.4% |
