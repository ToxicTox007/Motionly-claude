from uuid import UUID

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    # Checked in the route (app/services/signup_policy.py) so that every
    # refusal has its own error code; the bounds here only cap request size.
    full_name: str = Field(max_length=1000)
    email: str = Field(max_length=1000)
    password: str = Field(max_length=1000)


class SignupResponse(BaseModel):
    user_id: UUID
    email: str


class PasswordResetRequestRequest(BaseModel):
    # Only the size is bounded here; the email is normalized in the route and
    # an invalid one still produces the same 202 (no enumeration).
    email: str = Field(max_length=1000)


class PasswordResetRequestResponse(BaseModel):
    # The same answer whatever the email was: "if an account exists, an admin
    # will help you back in." Never confirms whether the account exists.
    status: str = "received"


class AdminUserRecord(BaseModel):
    id: str
    email: str
    banned: bool
    banned_until: str | None = None
    last_sign_in_at: str | None = None
    created_at: str | None = None


class AdminUserSearchResponse(BaseModel):
    users: list[AdminUserRecord]


class AdminResetRequestRecord(BaseModel):
    id: UUID
    email: str
    status: str
    created_at: str


class AdminResetRequestListResponse(BaseModel):
    requests: list[AdminResetRequestRecord]


class AdminCreateResetLinkRequest(BaseModel):
    email: str = Field(max_length=1000)
    request_id: UUID | None = None


class AdminResetLinkResponse(BaseModel):
    # Shown to the admin once, with a copy button. Valid for one hour; issuing
    # a new link invalidates any earlier one.
    link: str
    expires_in_seconds: int


class AdminBanRequest(BaseModel):
    user_id: UUID


class AdminBanResponse(BaseModel):
    user_id: str
    banned: bool


class AdminDismissRequestRequest(BaseModel):
    request_id: UUID
