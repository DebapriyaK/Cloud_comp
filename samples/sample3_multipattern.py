def generate_report(users, events):

    # filter active users manually
    active = []
    for user in users:
        if user["active"]:
            active.append(user)

    # sum up scores manually
    total = 0
    for user in active:
        total += user["score"]

    # build log string with +=
    log = ""
    for event in events:
        log += f"[{event['ts']}] {event['action']}\n"

    return active, total, log
