import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class ChildConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHILD_")
    value: str = "default"


class ParentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")
    child: ChildConfig = ChildConfig()


# If ChildConfig is initialized independently, it should pick up CHILD_VALUE
os.environ["CHILD_VALUE"] = "independent_child_value"
p = ParentConfig()
print(f"Child Value: {p.child.value}")

# What if we use the nested path?
os.environ["CHILD__VALUE"] = "nested_child_value"
p2 = ParentConfig()
print(f"Nested Child Value: {p2.child.value}")
