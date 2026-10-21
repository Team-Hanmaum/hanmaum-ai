"""One v2 runtime per app/process. No implicit receipt initialization or LLM calls."""

import asyncio

from app.v2.comparison import RequestDigester
from app.v2.config import V2Settings
from app.v2.receipts import ReceiptGate, SQLiteReceiptStore
from app.v2.resources import Capacity, Workers
from app.v2.services import AnalysisService


class StubProvider:
    async def analyze(self, request):
        return {"requestId": str(request.request_id), "suggestions": []}


class V2Runtime:
    def __init__(self, settings: V2Settings, app_env: str, service=None):
        self.settings = settings
        if settings.provider == "stub" and app_env == "prod":
            raise ValueError("v2 stub is restricted to local/test environments")
        self.service = service
        if service is None and settings.provider == "stub":
            store = SQLiteReceiptStore(
                settings.receipt_db_path, lock_timeout_seconds=settings.store_lock_timeout_seconds
            )
            digester = RequestDigester(
                keys=settings.hmac_keys, active_key_version=settings.active_hmac_key_version
            )
            self.service = AnalysisService(
                receipts=ReceiptGate(store, digester),
                provider=StubProvider(),
                timeout_seconds=settings.provider_timeout_seconds,
                worker_timeout_seconds=settings.worker_timeout_seconds,
            )
        self.admissions = Capacity(4)
        self.cpu = self.service.cpu if self.service else Workers(4)

    async def close(self):
        if self.service:
            tasks = set(self.service.tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.wait(tasks, timeout=1)
