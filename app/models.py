from pydantic import BaseModel
from typing import Optional


class LoginRequest(BaseModel):
    name: str
    password: str


class CreateAnnotatorRequest(BaseModel):
    """Admin-only: create a new annotator account with an initial password
    the Admin chooses and shares with them directly."""
    name: str
    role: str  # "Domain expert", "Native speaker", or "Admin"
    password: str


class SubmitLabelRequest(BaseModel):
    # No `annotator` field - identity comes from the verified session token,
    # never from client-supplied data.
    word: str
    sentence_id: str
    chosen_sense: str  # a sense_id, or "REJECT"


class ResolveRequest(BaseModel):
    # No `resolver` field - identity comes from the verified session token.
    word: str
    sentence_id: str
    final_sense: str
    note: Optional[str] = ""
