"""Health and runtime-mode discovery."""

from fastapi import APIRouter, Depends

from app.biodiversity.api.dependencies import application
from app.biodiversity.api.schemas import HealthView, RunModeView
from app.biodiversity.api.services.operations import Phase4Application
from app.biodiversity.run_models import WORKFLOW_VERSION


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthView)
async def health(
    service: Phase4Application = Depends(application),
) -> HealthView:
    return HealthView(
        workflow_version=WORKFLOW_VERSION,
        default_data_mode=service.settings.default_data_mode,
        default_model_mode=service.settings.default_model_mode,
        allowed_run_modes=[
            RunModeView(data_mode=data_mode, model_mode=model_mode)
            for data_mode, model_mode in service.settings.allowed_run_modes
        ],
        public_demo=service.settings.public_demo,
    )
