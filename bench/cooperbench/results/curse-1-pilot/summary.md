# curse-1: pass, latency, tokens and cost per arm

| arm | complete pairs | passed | rate | 95% Wilson | cut runs (errors) | rate, errors as failures | wall s (median) | agent s sum (median) | tokens per pair (mean) | output (mean, lower bound) | cost per pair (mean) | cost per pass | msgs per pair |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| solo | 4 | 3 | 75.0% | 30-95% | 1 | 60.0% (3/5) | 126.6 | 112.9 | 1,257,890 | 2,680 | $0.42 | $0.56 | 0.0 |
| coop | 3 | 2 | 66.7% | 21-94% | 1 | 50.0% (2/4) | 261.1 | 490.9 | 3,434,223 | 6,618 | $1.05 | $1.58 | 8.67 |
| sdlc | 3 | 2 | 66.7% | 21-94% | 1 | 50.0% (2/4) | 212.6 | 361.3 | 3,490,167 | 5,455 | $1.05 | $1.57 | 4.33 |
| coop-git | 3 | 2 | 66.7% | 21-94% | 1 | 50.0% (2/4) | 239.4 | 431.4 | 3,707,842 | 7,779 | $1.11 | $1.67 | 6.33 |
| team | 3 | 2 | 66.7% | 21-94% | 1 | 50.0% (2/4) | 210.8 | 335.0 | 3,331,599 | 11,091 | $1.06 | $1.59 | 4.0 |

## Paired against solo and coop, over the pairs both arms completed (b over a: below 1 means b is cheaper or faster)

| a | b | shared | both pass | only a | only b | neither | wall ratio | agent s ratio | tokens ratio | cost ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| solo | coop | 3 | 2 | 0 | 0 | 1 | 2.288 | 4.899 | 3.571 | 2.75 |
| solo | sdlc | 3 | 2 | 0 | 0 | 1 | 1.863 | 3.606 | 3.238 | 2.5 |
| solo | coop-git | 3 | 2 | 0 | 0 | 1 | 2.098 | 4.305 | 2.817 | 2.5 |
| solo | team | 3 | 2 | 0 | 0 | 1 | 1.848 | 3.343 | 2.942 | 2.75 |
| coop | solo | 3 | 2 | 0 | 0 | 1 | 0.437 | 0.204 | 0.28 | 0.364 |
| coop | sdlc | 3 | 2 | 0 | 0 | 1 | 0.814 | 0.736 | 0.907 | 0.909 |
| coop | coop-git | 3 | 2 | 0 | 0 | 1 | 0.917 | 0.879 | 0.789 | 0.909 |
| coop | team | 3 | 2 | 0 | 0 | 1 | 0.807 | 0.682 | 0.824 | 1.0 |

Prices (USD per million tokens): {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}; output tokens are a lower bound where the table says so (summary.json counts the pairs).

