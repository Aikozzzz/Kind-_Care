from typing import Generic, Literal, TypeVar

from pydantic import BaseModel


DataType = TypeVar("DataType")


class SuccessResponse(BaseModel, Generic[DataType]):
    success: Literal[True] = True
    message: str
    data: DataType


class FailureResponse(BaseModel, Generic[DataType]):
    success: Literal[False] = False
    message: str
    data: DataType
