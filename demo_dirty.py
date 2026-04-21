"""
demo_dirty.py  —  A realistic user-analytics service with several
carbon-inefficient patterns baked in. Run the analyzer against this file.
"""

# ── Pattern G5: known-list membership (confirmed) ────────────────────────
# A module-level list of privileged user IDs used for permission checks.
# As the user base grows this becomes an O(N) scan on every request.
ADMIN_IDS = [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010]


def is_admin(user_id):
    """Check whether a user has admin privileges."""
    if user_id in ADMIN_IDS:       # ADMIN_IDS is a known list -> CONFIRMED G5
        return True
    return False


# ── Pattern G4: recursive function ───────────────────────────────────────
def fibonacci(n):
    """Compute the Nth Fibonacci number (naively recursive)."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)


# ── Pattern G1: manual summation loop ────────────────────────────────────
def total_page_views(view_counts):
    """Sum up page-view counts across all tracked pages."""
    total = 0
    for count in view_counts:
        total += count
    return total


# ── Pattern G2: manual filter loop ───────────────────────────────────────
def get_active_sessions(sessions):
    """Return only the sessions that are still marked active."""
    active = []
    for session in sessions:
        if session["active"]:
            active.append(session)
    return active


# ── Pattern G3: manual transformation loop ───────────────────────────────
def normalize_scores(raw_scores):
    """Convert raw scores (0–1000) into a 0.0–1.0 normalised float."""
    normalised = []
    for score in raw_scores:
        normalised.append(score / 1000.0)
    return normalised


# ── Pattern G6: string building with += ──────────────────────────────────
def build_audit_log(events):
    """Serialise a list of audit events into a single log string."""
    log = ""
    for event in events:
        log += f"[{event['ts']}] {event['action']} by user {event['uid']}\n"
    return log


# ── Pattern G7: list used as a FIFO queue ────────────────────────────────
def drain_job_queue(pending_jobs):
    """
    Process jobs in FIFO order.
    list.pop(0) is O(N) — it shifts every remaining element left.
    """
    queue   = list(pending_jobs)
    results = []
    while queue:
        job = queue.pop(0)
        results.append(job["id"] * 2)
    return results


# ── Pattern G5: unknown-type parameter (possible) ────────────────────────
def check_permission(user_id, allowed_members):
    """
    Gate access to a resource. allowed_members is passed in from outside —
    the analyzer cannot confirm its type at static analysis time.
    """
    if user_id in allowed_members:
        return True
    return False

