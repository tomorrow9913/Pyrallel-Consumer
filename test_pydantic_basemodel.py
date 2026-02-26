import os

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChildModel(BaseModel):
    value: str = "default"


class ParentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")
    child: ChildModel = ChildModel()


os.environ["CHILD__VALUE"] = "env_child_value"
p = ParentConfig()
print(f"BaseModel Child Value: {p.child.value}")
