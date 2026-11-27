from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.runtime import Runtime


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


def not_found(thing: str, key: object) -> ApiError:
    return ApiError(404, "not_found", f"{thing} {key} does not exist")


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.runtime.database.session() as session:
        yield session


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]
SessionDep = Annotated[Session, Depends(get_session)]
