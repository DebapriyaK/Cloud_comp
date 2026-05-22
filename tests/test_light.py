# test_light.py
def count_hits(items):
    # Inefficient pattern: list membership inside loop (should suggest set)
    bad_list = list(range(1000))

    hits = 0
    for x in items:
        if x in bad_list:
            hits += 1
    return hits


if __name__ == "__main__":
    print(count_hits(range(2000)))