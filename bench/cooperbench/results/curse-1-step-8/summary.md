# curse-1: pass, latency, tokens and cost per arm

| arm | complete pairs | passed | rate | 95% Wilson | cut runs (errors) | rate, errors as failures | wall s (median) | agent s sum (median) | tokens per pair (mean) | output (mean, lower bound) | cost per pair (mean) | cost per pass | msgs per pair |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | 7 | 4 | 57.1% | 25-84% | 0 | 57.1% (4/7) | 199.4 | 175.4 | 1,752,808 | 19,854 | $0.72 | $1.27 | 0.0 |
| coop | 7 | 5 | 71.4% | 36-92% | 0 | 71.4% (5/7) | 285.4 | 361.4 | 2,738,099 | 27,498 | $1.11 | $1.56 | 5.43 |
| sdlc | 7 | 5 | 71.4% | 36-92% | 0 | 71.4% (5/7) | 444.3 | 687.8 | 3,385,916 | 20,268 | $1.17 | $1.64 | 3.43 |
| coop-git | 7 | 5 | 71.4% | 36-92% | 0 | 71.4% (5/7) | 287.5 | 500.9 | 3,979,081 | 28,751 | $1.37 | $1.92 | 5.57 |
| team | 7 | 5 | 71.4% | 36-92% | 0 | 71.4% (5/7) | 269.5 | 436.9 | 3,831,729 | 28,417 | $1.38 | $1.93 | 4.29 |

## Paired against solo and coop, over the pairs both arms completed (b over a: below 1 means b is cheaper or faster)

| a | b | shared | both pass | only a | only b | neither | wall ratio | agent s ratio | tokens ratio | cost ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| solo | coop | 7 | 4 | 0 | 1 | 2 | 1.431 | 2.06 | 1.788 | 1.667 |
| solo | sdlc | 7 | 4 | 0 | 1 | 2 | 2.228 | 3.921 | 2.057 | 2.0 |
| solo | coop-git | 7 | 4 | 0 | 1 | 2 | 1.442 | 2.856 | 2.088 | 2.167 |
| solo | team | 7 | 4 | 0 | 1 | 2 | 1.352 | 2.491 | 1.972 | 2.167 |
| coop | solo | 7 | 4 | 1 | 0 | 2 | 0.699 | 0.485 | 0.559 | 0.6 |
| coop | sdlc | 7 | 5 | 0 | 0 | 2 | 1.557 | 1.903 | 1.151 | 1.2 |
| coop | coop-git | 7 | 5 | 0 | 0 | 2 | 1.007 | 1.386 | 1.168 | 1.3 |
| coop | team | 7 | 5 | 0 | 0 | 2 | 0.944 | 1.209 | 1.103 | 1.3 |

Prices (USD per million tokens): {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}; output tokens are a lower bound where the table says so (summary.json counts the pairs).

