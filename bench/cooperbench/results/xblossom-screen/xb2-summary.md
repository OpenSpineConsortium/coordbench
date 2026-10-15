# xb2: pass, latency, tokens and cost per arm

| arm | complete pairs | passed | rate | 95% Wilson | cut runs (errors) | rate, errors as failures | wall s (median) | agent s sum (median) | tokens per pair (mean) | output (mean, lower bound) | cost per pair (mean) | cost per pass | msgs per pair |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | 2 | 2 | 100.0% | 34-100% | 0 | 100.0% (2/2) | 435.7 | 423.5 | 2,213,135 | 45,420 | $1.21 | $1.21 | 0.0 |
| team | 2 | 2 | 100.0% | 34-100% | 0 | 100.0% (2/2) | 478.8 | 891.1 | 6,132,440 | 60,318 | $2.27 | $2.27 | 4.5 |
| xblossom | 2 | 2 | 100.0% | 34-100% | 0 | 100.0% (2/2) | 456.6 | 825.0 | 6,540,022 | 67,919 | $2.44 | $2.44 | 4.0 |

## Paired against solo and team, over the pairs both arms completed (b over a: below 1 means b is cheaper or faster)

| a | b | shared | both pass | only a | only b | neither | wall ratio | agent s ratio | tokens ratio | net tokens ratio | cost ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | team | 2 | 2 | 0 | 0 | 0 | 1.099 | 2.104 | 2.771 | 2.134 | 1.875 |
| solo | xblossom | 2 | 2 | 0 | 0 | 0 | 1.048 | 1.948 | 2.955 | 2.446 | 2.017 |
| team | solo | 2 | 2 | 0 | 0 | 0 | 0.91 | 0.475 | 0.361 | 0.469 | 0.533 |
| team | xblossom | 2 | 2 | 0 | 0 | 0 | 0.954 | 0.926 | 1.066 | 1.147 | 1.076 |

## What the session route did (audit.json)

| arm | harness errors (left out) | audited pairs | API calls per pair (mean) | fixed prompt share | net tokens per pair (median) | pairs with a refusal | efforts |
|---|---|---|---|---|---|---|---|
| solo | 0 | 2 | 16.0 | 59% | 913,477 | 0 | xhigh |
| team | 0 | 2 | 51.5 | 68% | 1,949,011 | 0 | xhigh |
| xblossom | 0 | 2 | 53.0 | 66% | 2,234,638 | 0 | xhigh |

Prices (USD per million tokens): {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}; output tokens are a lower bound where the table says so (summary.json counts the pairs).

