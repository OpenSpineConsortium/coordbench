# curse-1: pass, latency, tokens and cost per arm

| arm | complete pairs | passed | rate | 95% Wilson | cut runs (errors) | rate, errors as failures | wall s (median) | agent s sum (median) | tokens per pair (mean) | output (mean, lower bound) | cost per pair (mean) | cost per pass | msgs per pair |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | 36 | 27 | 75.0% | 59-86% | 0 | 75.0% (27/36) | 191.7 | 166.2 | 1,464,867 | 15,146 | $0.62 | $0.82 | 0.0 |
| coop | 36 | 25 | 69.4% | 53-82% | 0 | 69.4% (25/36) | 253.6 | 400.1 | 3,114,175 | 25,507 | $1.19 | $1.72 | 6.19 |
| sdlc | 36 | 26 | 72.2% | 56-84% | 0 | 72.2% (26/36) | 390.6 | 587.7 | 3,825,066 | 23,681 | $1.33 | $1.84 | 3.75 |
| coop-git | 36 | 26 | 72.2% | 56-84% | 0 | 72.2% (26/36) | 265.4 | 482.3 | 3,795,126 | 24,441 | $1.32 | $1.83 | 6.19 |
| team | 35 | 26 | 74.3% | 58-86% | 0 | 74.3% (26/35) | 256.7 | 436.9 | 3,658,283 | 24,448 | $1.30 | $1.75 | 4.23 |

## Paired against solo and coop, over the pairs both arms completed (b over a: below 1 means b is cheaper or faster)

| a | b | shared | both pass | only a | only b | neither | wall ratio | agent s ratio | tokens ratio | net tokens ratio | cost ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | coop | 36 | 23 | 4 | 2 | 7 | 1.323 | 2.407 | 2.28 | 2.174 | 1.978 |
| solo | sdlc | 36 | 24 | 3 | 2 | 7 | 2.037 | 3.536 | 2.828 | 3.059 | 2.252 |
| solo | coop-git | 36 | 25 | 2 | 1 | 8 | 1.384 | 2.902 | 2.815 | 3.129 | 2.309 |
| solo | team | 35 | 24 | 2 | 2 | 7 | 1.395 | 2.656 | 2.833 | 2.859 | 2.312 |
| coop | solo | 36 | 23 | 2 | 4 | 7 | 0.756 | 0.415 | 0.439 | 0.46 | 0.506 |
| coop | sdlc | 36 | 24 | 1 | 2 | 9 | 1.54 | 1.469 | 1.24 | 1.407 | 1.139 |
| coop | coop-git | 36 | 24 | 1 | 2 | 9 | 1.046 | 1.205 | 1.235 | 1.44 | 1.167 |
| coop | team | 35 | 25 | 0 | 1 | 9 | 0.983 | 1.096 | 1.236 | 1.352 | 1.178 |

## What the session route did (audit.json)

| arm | harness errors (left out) | audited pairs | API calls per pair (mean) | fixed prompt share | net tokens per pair (median) | pairs with a refusal | efforts |
|---|---|---|---|---|---|---|---|
| solo | 0 | 36 | 14.75 | 74% | 246,148 | 0 | xhigh |
| coop | 0 | 36 | 32.92 | 78% | 535,002 | 10 | xhigh |
| sdlc | 0 | 36 | 39.56 | 76% | 752,978 | 1 | xhigh |
| coop-git | 0 | 36 | 39.42 | 77% | 770,141 | 10 | xhigh |
| team | 0 | 35 | 38.57 | 78% | 698,337 | 0 | xhigh |

Prices (USD per million tokens): {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}; output tokens are a lower bound where the table says so (summary.json counts the pairs).

