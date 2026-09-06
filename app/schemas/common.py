"""Shared response shapes and validators."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator

from app.security.url_guard import UnsafeURLError, validate_url

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    detail: str


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class SafeURL(BaseModel):
    """Mixin for request bodies carrying a user-supplied URL.

    URLs from a request body end up being fetched, so they go through the same
    SSRF policy as everything else rather than being trusted because a human
    typed them.
    """

    url: str

    @field_validator("url")
    @classmethod
    def _validate(cls, value: str) -> str:
        try:
            return validate_url(value).url
        except UnsafeURLError as exc:
            raise ValueError(str(exc)) from exc


def validate_optional_url(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        return validate_url(str(value)).url
    except UnsafeURLError as exc:
        raise ValueError(str(exc)) from exc
