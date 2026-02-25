import uuid


def make_id() -> str:
    """Make a random id."""
    return str(uuid.uuid4())
