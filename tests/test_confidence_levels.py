"""
TEST CASE 3: Confidence level discrimination.

Expected findings: 3
  - Line 15: G5 . Membership  |  CONFIRMED  (list_a is a known list literal)
  - Line 20: G5 . Membership  |  POSSIBLE   (param_b is a function parameter)
  - Line 25: G5 . Membership  |  (nothing)  (set_c is a set -- no issue)

This isolates the CONFIRMED vs POSSIBLE distinction in one file.
All three membership tests look syntactically identical: x in y
The difference is purely in what y resolves to via static type inference.
"""

list_a = [10, 20, 30, 40, 50]    # confirmed list

def check_list(x):
    return x in list_a            # SHOULD fire: CONFIRMED

def check_param(x, param_b):
    return x in param_b           # SHOULD fire: POSSIBLE (type unknown)

set_c = {10, 20, 30, 40, 50}     # confirmed set

def check_set(x):
    return x in set_c             # should NOT fire: already O(1)
