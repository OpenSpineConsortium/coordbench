# curse-1: pass, latency, tokens and cost per arm

| arm | complete pairs | passed | rate | 95% Wilson | cut runs (errors) | rate, errors as failures | wall s (median) | agent s sum (median) | tokens per pair (mean) | output (mean, lower bound) | cost per pair (mean) | cost per pass | msgs per pair |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | 8 | 5 | 62.5% | 31-86% | 0 | 62.5% (5/8) | 219.1 | 194.5 | 1,589,726 | 17,024 | $0.67 | $1.07 | 0.0 |
| coop | 8 | 7 | 87.5% | 53-98% | 0 | 87.5% (7/8) | 265.8 | 355.6 | 3,114,874 | 29,673 | $1.26 | $1.44 | 5.75 |
| sdlc | 8 | 7 | 87.5% | 53-98% | 0 | 87.5% (7/8) | 406.7 | 662.4 | 4,228,066 | 29,680 | $1.49 | $1.70 | 3.88 |
| coop-git | 8 | 6 | 75.0% | 41-93% | 0 | 75.0% (6/8) | 307.7 | 525.3 | 4,246,799 | 29,162 | $1.49 | $1.98 | 6.25 |
| team | 8 | 7 | 87.5% | 53-98% | 0 | 87.5% (7/8) | 263.1 | 442.5 | 3,854,933 | 26,930 | $1.40 | $1.60 | 3.88 |

## Paired against solo and coop, over the pairs both arms completed (b over a: below 1 means b is cheaper or faster)

| a | b | shared | both pass | only a | only b | neither | wall ratio | agent s ratio | tokens ratio | net tokens ratio | cost ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | coop | 8 | 5 | 0 | 2 | 1 | 1.213 | 1.828 | 1.723 | 1.976 | 1.813 |
| solo | sdlc | 8 | 5 | 0 | 2 | 1 | 1.857 | 3.405 | 2.356 | 3.105 | 2.115 |
| solo | coop-git | 8 | 5 | 0 | 1 | 2 | 1.405 | 2.701 | 2.588 | 2.929 | 2.21 |
| solo | team | 8 | 5 | 0 | 2 | 1 | 1.201 | 2.275 | 2.145 | 2.337 | 1.956 |
| coop | solo | 8 | 5 | 2 | 0 | 1 | 0.824 | 0.547 | 0.58 | 0.506 | 0.552 |
| coop | sdlc | 8 | 7 | 0 | 0 | 1 | 1.53 | 1.863 | 1.367 | 1.571 | 1.166 |
| coop | coop-git | 8 | 6 | 1 | 0 | 1 | 1.158 | 1.477 | 1.501 | 1.482 | 1.219 |
| coop | team | 8 | 7 | 0 | 0 | 1 | 0.99 | 1.244 | 1.244 | 1.183 | 1.079 |

## What the session route did (audit.json)

| arm | harness errors (left out) | audited pairs | API calls per pair (mean) | fixed prompt share | net tokens per pair (median) | pairs with a refusal | efforts |
|---|---|---|---|---|---|---|---|
| solo | 0 | 8 | 15.25 | 75% | 273,830 | 0 | xhigh |
| coop | 0 | 8 | 30.88 | 79% | 540,980 | 1 | xhigh |
| sdlc | 0 | 8 | 41.12 | 74% | 850,134 | 0 | xhigh |
| coop-git | 0 | 8 | 40.88 | 76% | 801,916 | 2 | xhigh |
| team | 0 | 8 | 37.25 | 78% | 639,889 | 0 | xhigh |

Prices (USD per million tokens): {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}; output tokens are a lower bound where the table says so (summary.json counts the pairs).

