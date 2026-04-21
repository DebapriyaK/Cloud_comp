"""
TEST CASE 2: Already-optimized code.

Expected findings: 0

This verifies the analyzer does NOT produce false positives.
Every pattern here is already the recommended implementation.
"""
from collections import deque

# G1 equivalent -- already uses builtin
def total_requests(counts):
    return sum(counts)

# G2 equivalent -- already uses list comprehension
def get_active(sessions):
    return [s for s in sessions if s["active"]]

# G3 equivalent -- already uses list comprehension
def normalize(scores):
    return [s / 1000.0 for s in scores]

# G5 equivalent -- already a set, no O(N) scan
ALLOWED = {101, 102, 103, 104, 105}

def has_access(user_id):
    return user_id in ALLOWED

# G6 equivalent -- already uses join
def build_log(events):
    return "\n".join(f"{e['ts']}: {e['action']}" for e in events)

# G7 equivalent -- already uses deque
def drain_queue(items):
    q = deque(items)
    results = []
    while q:
        results.append(q.popleft())
    return results

# Loops that should NOT fire: they don't match any pattern
def count_items(data):
    count = 0
    for _ in data:
        count += 1       # += 1 on a counter — not a sum of the iterable itself
    return count

def accumulate_product(data):
    result = 1
    for x in data:
        result *= x      # *= not +=, so not a summation pattern
    return result
