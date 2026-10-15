# curse-1: pass, latency, tokens and cost per arm

| arm | complete pairs | passed | rate | 95% Wilson | cut runs (errors) | rate, errors as failures | wall s (median) | agent s sum (median) | tokens per pair (mean) | output (mean, lower bound) | cost per pair (mean) | cost per pass | msgs per pair |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | 8 | 5 | 62.5% | 31-86% | 0 | 62.5% (5/8) | 219.1 | 201.4 | 1,737,924 | 18,812 | $0.72 | $1.15 | 0.0 |
| coop | 8 | 6 | 75.0% | 41-93% | 0 | 75.0% (6/8) | 335.3 | 404.6 | 3,016,430 | 27,290 | $1.17 | $1.56 | 6.12 |
| sdlc | 8 | 6 | 75.0% | 41-93% | 0 | 75.0% (6/8) | 529.4 | 737.4 | 3,575,799 | 20,237 | $1.23 | $1.63 | 3.75 |
| coop-git | 8 | 6 | 75.0% | 41-93% | 0 | 75.0% (6/8) | 307.7 | 525.3 | 4,116,160 | 28,024 | $1.41 | $1.87 | 5.62 |
| team | 8 | 6 | 75.0% | 41-93% | 0 | 75.0% (6/8) | 295.7 | 442.5 | 3,741,854 | 26,866 | $1.35 | $1.80 | 4.12 |

## Paired against solo and coop, over the pairs both arms completed (b over a: below 1 means b is cheaper or faster)

| a | b | shared | both pass | only a | only b | neither | wall ratio | agent s ratio | tokens ratio | cost ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| solo | coop | 8 | 5 | 0 | 1 | 2 | 1.53 | 2.009 | 1.883 | 1.833 |
| solo | sdlc | 8 | 5 | 0 | 1 | 2 | 2.416 | 3.661 | 2.153 | 2.0 |
| solo | coop-git | 8 | 5 | 0 | 1 | 2 | 1.404 | 2.608 | 2.452 | 2.333 |
| solo | team | 8 | 5 | 0 | 1 | 2 | 1.35 | 2.197 | 1.991 | 2.0 |
| coop | solo | 8 | 5 | 1 | 0 | 2 | 0.653 | 0.498 | 0.531 | 0.545 |
| coop | sdlc | 8 | 6 | 0 | 0 | 2 | 1.579 | 1.823 | 1.143 | 1.091 |
| coop | coop-git | 8 | 6 | 0 | 0 | 2 | 0.918 | 1.298 | 1.302 | 1.273 |
| coop | team | 8 | 6 | 0 | 0 | 2 | 0.882 | 1.094 | 1.057 | 1.091 |

Prices (USD per million tokens): {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}; output tokens are a lower bound where the table says so (summary.json counts the pairs).

