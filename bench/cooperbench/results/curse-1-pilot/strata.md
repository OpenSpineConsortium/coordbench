| language | conflict | published | pairs |
|---|---|---|---|
| go | conflict | none | 2 |
| python | clean | few | 8 |
| python | clean | many | 3 |
| python | clean | none | 1 |
| python | conflict | few | 13 |
| python | conflict | many | 2 |
| python | conflict | none | 8 |
| python+c | clean | few | 1 |
| python+c | conflict | few | 1 |
| python+c | conflict | none | 4 |
| python+rust | conflict | few | 1 |
| python+rust | conflict | none | 2 |
| rust | conflict | few | 2 |
| rust | conflict | none | 2 |

| pair | language | gold merge | gold lines | size | published coop passes / runs | paper's Claude |
|---|---|---|---|---|---|---|
| dottxt_ai_outlines_task/1655/f6_f7 | python | conflict | 13 | small | 4/8 | fail |
| dottxt_ai_outlines_task/1655/f7_f10 | python | conflict | 13 | small | 5/8 | pass |
| dottxt_ai_outlines_task/1706/f4_f6 | python | conflict | 35 | small | 1/8 | fail |
| dottxt_ai_outlines_task/1706/f5_f8 | python | conflict | 171 | large | 1/8 | fail |
| dspy_task/8394/f3_f4 | python | conflict | 171 | large | 2/8 | fail |
| dspy_task/8394/f3_f5 | python | conflict | 164 | large | 0/8 | fail |
| go_chi_task/26/f1_f2 | go | conflict | 112 | medium | 0/8 | fail |
| go_chi_task/56/f1_f5 | go | conflict | 245 | large | 0/8 | fail |
| huggingface_datasets_task/3997/f2_f4 | python | clean | 29 | small | 0/8 | fail |
| huggingface_datasets_task/6252/f4_f6 | python | clean | 11 | small | 8/8 | pass |
| llama_index_task/18813/f1_f5 | python | conflict | 35 | small | 0/8 | fail |
| llama_index_task/18813/f2_f5 | python | conflict | 36 | small | 2/8 | fail |
| openai_tiktoken_task/0/f2_f9 | python+rust | conflict | 133 | large | 0/8 | fail |
| openai_tiktoken_task/0/f3_f6 | python+rust | conflict | 43 | small | 0/8 | fail |
| openai_tiktoken_task/0/f6_f8 | python+rust | conflict | 55 | small | 2/8 | fail |
| pallets_click_task/2068/f1_f6 | python | conflict | 116 | large | 2/8 | fail |
| pallets_click_task/2068/f2_f10 | python | conflict | 34 | small | 0/8 | fail |
| pallets_click_task/2068/f5_f11 | python | conflict | 221 | large | 0/8 | fail |
| pallets_click_task/2068/f5_f7 | python | conflict | 133 | large | 2/8 | fail |
| pallets_click_task/2068/f6_f7 | python | conflict | 89 | medium | 0/8 | fail |
| pallets_click_task/2068/f7_f10 | python | conflict | 23 | small | 0/8 | fail |
| pallets_click_task/2800/f1_f3 | python | conflict | 74 | medium | 1/8 | fail |
| pallets_click_task/2800/f1_f7 | python | conflict | 100 | medium | 0/8 | fail |
| pallets_click_task/2800/f2_f7 | python | conflict | 93 | medium | 2/8 | pass |
| pallets_click_task/2800/f6_f7 | python | clean | 137 | large | 3/8 | pass |
| pallets_click_task/2956/f1_f8 | python | clean | 163 | large | 1/8 | pass |
| pallets_jinja_task/1465/f2_f3 | python | conflict | 51 | small | 2/8 | fail |
| pallets_jinja_task/1465/f2_f6 | python | conflict | 54 | small | 1/8 | pass |
| pallets_jinja_task/1559/f1_f8 | python | clean | 252 | large | 2/8 | pass |
| pallets_jinja_task/1559/f4_f10 | python | clean | 56 | medium | 3/8 | pass |
| pallets_jinja_task/1559/f4_f8 | python | clean | 263 | large | 1/8 | pass |
| pallets_jinja_task/1559/f6_f9 | python | clean | 149 | large | 3/8 | pass |
| pallets_jinja_task/1621/f1_f6 | python | conflict | 97 | medium | 2/8 | fail |
| pallets_jinja_task/1621/f2_f9 | python | conflict | 153 | large | 2/8 | fail |
| pallets_jinja_task/1621/f3_f5 | python | clean | 68 | medium | 6/8 | pass |
| pallets_jinja_task/1621/f4_f10 | python | clean | 84 | medium | 2/8 | pass |
| pallets_jinja_task/1621/f4_f5 | python | clean | 82 | medium | 2/8 | pass |
| pallets_jinja_task/1621/f6_f7 | python | clean | 52 | small | 6/8 | pass |
| pillow_task/25/f1_f5 | python+c | clean | 33 | small | 2/8 | fail |
| pillow_task/25/f2_f3 | python+c | conflict | 30 | small | 3/8 | pass |
| pillow_task/68/f1_f5 | python+c | conflict | 61 | medium | 0/8 | fail |
| pillow_task/290/f2_f3 | python+c | conflict | 95 | medium | 0/8 | fail |
| pillow_task/290/f3_f5 | python+c | conflict | 133 | large | 0/8 | fail |
| pillow_task/290/f4_f5 | python+c | conflict | 122 | large | 0/8 | fail |
| samuelcolvin_dirty_equals_task/43/f2_f3 | python | conflict | 100 | medium | 0/8 | fail |
| samuelcolvin_dirty_equals_task/43/f3_f7 | python | conflict | 98 | medium | 1/8 | fail |
| typst_task/6554/f1_f9 | rust | conflict | 60 | medium | 1/3 | - |
| typst_task/6554/f4_f9 | rust | conflict | 81 | medium | 0/3 | - |
| typst_task/6554/f5_f9 | rust | conflict | 93 | medium | 1/3 | - |
| typst_task/6554/f7_f8 | rust | conflict | 136 | large | 0/3 | - |
