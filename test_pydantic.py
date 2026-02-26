import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class ChildConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHILD_")
    value: str = "default"


class ParentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")
    child: ChildConfig = ChildConfig()
    top_value: str = "top_default"


# Test 1: Parent no prefix, Child has prefix
os.environ["CHILD__VALUE"] = "env_child_value"
os.environ["TOP_VALUE"] = "env_top_value"

p = ParentConfig()
print(f"Test 1 - Child Value: {p.child.value}")
print(f"Test 1 - Top Value: {p.top_value}")

# Test 2: Does the child's prefix matter when nested?
os.environ["CHILD__CHILD_VALUE"] = "env_child_with_prefix"
p2 = ParentConfig()
print(f"Test 2 - Child Value (with prefix): {p2.child.value}")


# Test 3: What if we use the child's prefix as the field name?
class ParentWithPrefix(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PARENT_", env_nested_delimiter="__")
    child: ChildConfig = ChildConfig()


os.environ["PARENT_CHILD__VALUE"] = "parent_child_value"
p3 = ParentWithPrefix()
print(f"Test 3 - Parent Prefix + Child Field: {p3.child.value}")
