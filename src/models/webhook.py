from pydantic import BaseModel


class WebhookHealth(BaseModel):
    status: str
    fail_count: int


class Webhook(BaseModel):
    id: str
    userid: int
    team_id: int
    endpoint: str
    client_id: str
    events: list[str]
    task_id: str | None = None
    list_id: str | None = None
    folder_id: str | None = None
    space_id: str | None = None
    view_id: str | None = None
    health: WebhookHealth
    secret: str


class WebhookRegistrationResponse(BaseModel):
    id: str
    webhook: Webhook
