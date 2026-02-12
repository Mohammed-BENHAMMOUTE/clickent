from pydantic import BaseModel, Field


class TaskStatus(BaseModel):
    status: str
    color: str
    type: str
    orderindex: int


class TaskPriority(BaseModel):
    id: str
    priority: str
    color: str
    orderindex: str


class TaskCreator(BaseModel):
    id: int
    username: str
    email: str
    color: str | None = None
    profile_picture: str | None = Field(None, alias="profilePicture")


class TaskAssignee(BaseModel):
    id: int
    username: str
    email: str
    color: str | None = None
    initials: str | None = None
    profile_picture: str | None = Field(None, alias="profilePicture")


class ClickUpTask(BaseModel):
    id: str
    name: str
    description: str | None = None
    text_content: str | None = None
    status: TaskStatus
    creator: TaskCreator
    assignees: list[TaskAssignee] = []
    priority: TaskPriority | None = None
    due_date: str | None = None
    start_date: str | None = None
    date_created: str | None = None
    date_updated: str | None = None
    url: str | None = None
    clickup_list: dict | None = Field(default=None, alias="list")
    folder: dict | None = None
    space: dict | None = None
    tags: list[dict] = []

    model_config = {"extra": "ignore"}
