from pydantic import BaseModel, ConfigDict,Field

class ReadFileArgs(BaseModel):
    model_config = ConfigDict(strict = True,extra="forbid")

    path:str=Field(min_length = 1)
    limit:int|None =Field(default=None,gt=0)