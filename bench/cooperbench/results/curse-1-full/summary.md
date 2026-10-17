# curse-1: pass, latency, tokens and cost per arm

| arm | complete pairs | passed | rate | 95% Wilson | cut runs (errors) | rate, errors as failures | wall s (median) | agent s sum (median) | tokens per pair (mean) | output (mean, lower bound) | cost per pair (mean) | cost per pass | msgs per pair |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | 43 | 33 | 76.7% | 62-87% | 0 | 76.7% (33/43) | 184.1 | 164.5 | 1,467,582 | 14,083 | $0.61 | $0.79 | 0.0 |
| coop | 43 | 30 | 69.8% | 55-81% | 0 | 69.8% (30/43) | 246.1 | 398.7 | 3,126,300 | 24,142 | $1.18 | $1.69 | 6.07 |
| sdlc | 43 | 32 | 74.4% | 60-85% | 0 | 74.4% (32/43) | 390.8 | 588.7 | 3,916,476 | 22,004 | $1.33 | $1.79 | 3.79 |
| coop-git | 43 | 32 | 74.4% | 60-85% | 0 | 74.4% (32/43) | 267.1 | 482.8 | 3,828,823 | 22,870 | $1.32 | $1.77 | 6.12 |
| team | 43 | 33 | 76.7% | 62-87% | 0 | 76.7% (33/43) | 261.9 | 436.9 | 3,769,663 | 23,107 | $1.32 | $1.72 | 4.3 |

## Paired against solo and coop, over the pairs both arms completed (b over a: below 1 means b is cheaper or faster)

| a | b | shared | both pass | only a | only b | neither | wall ratio | agent s ratio | tokens ratio | net tokens ratio | cost ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | coop | 43 | 28 | 5 | 2 | 8 | 1.337 | 2.424 | 2.268 | 2.232 | 1.963 |
| solo | sdlc | 43 | 30 | 3 | 2 | 8 | 2.123 | 3.578 | 2.824 | 3.111 | 2.362 |
| solo | coop-git | 43 | 31 | 2 | 1 | 9 | 1.451 | 2.934 | 2.837 | 3.156 | 2.384 |
| solo | team | 43 | 31 | 2 | 2 | 8 | 1.423 | 2.656 | 2.925 | 2.889 | 2.304 |
| coop | solo | 43 | 28 | 2 | 5 | 8 | 0.748 | 0.413 | 0.441 | 0.448 | 0.51 |
| coop | sdlc | 43 | 29 | 1 | 3 | 10 | 1.588 | 1.476 | 1.245 | 1.394 | 1.203 |
| coop | coop-git | 43 | 29 | 1 | 3 | 10 | 1.085 | 1.211 | 1.251 | 1.414 | 1.215 |
| coop | team | 43 | 30 | 0 | 3 | 10 | 1.064 | 1.096 | 1.29 | 1.294 | 1.174 |

## What the session route did (audit.json)

| arm | harness errors (left out) | audited pairs | API calls per pair (mean) | fixed prompt share | net tokens per pair (median) | pairs with a refusal | efforts |
|---|---|---|---|---|---|---|---|
| solo | 0 | 43 | 14.63 | 74% | 248,019 | 0 | xhigh |
| coop | 0 | 43 | 32.79 | 78% | 553,581 | 12 | xhigh |
| sdlc | 0 | 43 | 39.93 | 75% | 771,608 | 2 | xhigh |
| coop-git | 0 | 43 | 39.4 | 76% | 782,732 | 14 | xhigh |
| team | 0 | 43 | 39.28 | 77% | 716,539 | 0 | xhigh |

Prices (USD per million tokens): {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}; output tokens are a lower bound where the table says so (summary.json counts the pairs).

