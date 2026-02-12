from pydantic import BaseModel, Field


class WebhookUser(BaseModel):
    id: int
    username: str
    email: str
    color: str | None = None
    initials: str | None = None
    profile_picture: str | None = Field(None, alias="profilePicture")

    

class StatusData(BaseModel):
    status: str | None = None
    color: str | None = None
    type: str | None = None
    orderindex: int | None = None


class HistoryItemData(BaseModel):
    status_type: str | None = None


class HistoryItem(BaseModel):
    id: str
    type: int
    date: str
    field: str
    parent_id: str
    data: HistoryItemData | dict = {}
    source: str | None = None
    user: WebhookUser
    before: StatusData | None = None
    after: StatusData | None = None


class TaskCreatedEvent(BaseModel):
    event: str
    history_items: list[HistoryItem]
    task_id: str
    webhook_id: str
