from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from shared.schemas import RefineRequest, RefineResponse

from ..db import Database
from ..deps import get_db

router = APIRouter(tags=["prompt"])


@router.post("/prompt/refine", response_model=RefineResponse)
def refine_prompt(req: RefineRequest, request: Request, db: Database = Depends(get_db)):
    refiner = request.app.state.refiner
    context = dict(req.context)
    context.setdefault("approved_captions", db.approved_captions(5))
    res = refiner.refine(req.text, context)
    return RefineResponse(original=req.text, refined=res["refined"], warnings=res["warnings"],
                          suggested_params=res["suggested_params"], refiner=refiner.name)
