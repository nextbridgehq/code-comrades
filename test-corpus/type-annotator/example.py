def get_name(user):
    return user.name


def add(a, b):
    return a + b


def parse_csv(spec):
    if not spec:
        return []
    return [p.strip() for p in spec.split(",") if p.strip()]


def build_config(**kwargs):
    return dict(kwargs)
