"""Department model — organizational container for personas."""

from pydantic import BaseModel


class Department(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: str | None = None
