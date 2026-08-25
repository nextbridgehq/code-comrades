def get_name(user):
    return user.name


def add(a, b):
    return a + b


def parse_csv(spec):
    if not spec:
        return []
    return [p.strip() for p in spec.split(",") if p.strip()]


def merge_intervals(intervals):
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda pair: pair[0])
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def is_even(n):
    return n % 2 == 0
