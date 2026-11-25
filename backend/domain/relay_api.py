from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import require
from app.auth.roles import Role
from app.delivery.contracts import describe
from domain.relay import release_edition


router = APIRouter(tags=['Venue editions'])
reviewer = require(Role.reviewer)


class EditionReleaseRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    edition_key: str = Field(min_length=1, max_length=70)


@router.post('/runs/{run_id}/editions')
def release(run_id: int, payload: EditionReleaseRequest, response: Response, user=Depends(reviewer)):
    delivery, created = release_edition(run_id, payload.edition_key, user.email)
    response.status_code = 201 if created else 200
    return describe(delivery)
