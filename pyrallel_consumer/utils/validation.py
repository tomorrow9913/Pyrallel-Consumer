import re

_TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_topic_name(value: str) -> str:
    if not isinstance(value, str) or not _TOPIC_PATTERN.match(value):
        raise ValueError("Invalid topic name")
    return value
