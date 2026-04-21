def process_jobs(pending):
    queue = list(pending)
    results = []
    while queue:
        job = queue.pop(0)
        results.append(job * 2)
    return results
