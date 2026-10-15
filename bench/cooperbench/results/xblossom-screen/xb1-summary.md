# xb1: pass, latency, tokens and cost per arm

| arm | complete pairs | passed | rate | 95% Wilson | cut runs (errors) | rate, errors as failures | wall s (median) | agent s sum (median) | tokens per pair (mean) | output (mean, lower bound) | cost per pair (mean) | cost per pass | msgs per pair |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | 2 | 1 | 50.0% | 9-91% | 0 | 50.0% (1/2) | 401.6 | 389.4 | 2,130,966 | 42,168 | $1.13 | $2.25 | 0.0 |
| team | 2 | 2 | 100.0% | 34-100% | 0 | 100.0% (2/2) | 324.9 | 572.4 | 4,503,747 | 49,070 | $1.77 | $1.77 | 4.5 |
| xblossom | 2 | 2 | 100.0% | 34-100% | 0 | 100.0% (2/2) | 417.9 | 741.9 | 6,897,260 | 63,638 | $2.45 | $2.45 | 4.5 |

## Paired against solo and team, over the pairs both arms completed (b over a: below 1 means b is cheaper or faster)

| a | b | shared | both pass | only a | only b | neither | wall ratio | agent s ratio | tokens ratio | net tokens ratio | cost ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | team | 2 | 1 | 0 | 1 | 0 | 0.809 | 1.47 | 2.113 | 1.59 | 1.57 |
| solo | xblossom | 2 | 1 | 0 | 1 | 0 | 1.041 | 1.905 | 3.237 | 2.858 | 2.178 |
| team | solo | 2 | 1 | 1 | 0 | 0 | 1.236 | 0.68 | 0.473 | 0.629 | 0.637 |
| team | xblossom | 2 | 2 | 0 | 0 | 0 | 1.286 | 1.296 | 1.531 | 1.798 | 1.387 |

## What the session route did (audit.json)

| arm | harness errors (left out) | audited pairs | API calls per pair (mean) | fixed prompt share | net tokens per pair (median) | pairs with a refusal | efforts |
|---|---|---|---|---|---|---|---|
| solo | 0 | 2 | 16.5 | 63% | 792,976 | 0 | xhigh |
| team | 0 | 2 | 40.0 | 72% | 1,260,730 | 0 | xhigh |
| xblossom | 0 | 2 | 57.0 | 67% | 2,266,264 | 1 | xhigh |

Prices (USD per million tokens): {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}; output tokens are a lower bound where the table says so (summary.json counts the pairs).

