from collections import deque

ALLOWED_USERS = {1001, 1002, 1003, 1004, 1005,
                 1006, 1007, 1008, 1009, 1010}

def check_permission(user_id):
    return user_id in ALLOWED_USERS

def process_jobs(pending):
    queue = deque(pending)
    results = []
    while queue:
        results.append(queue.popleft() * 2)
    return results

def generate_report(users, events):
    active = [u for u in users if u["active"]]
    total  = sum(u["score"] for u in active)
    log    = "".join(f"[{e['ts']}] {e['action']}\n" for e in events)
    return active, total, log
