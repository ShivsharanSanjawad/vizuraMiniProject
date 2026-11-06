import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from database import database, save_meeting_doc

class Session:
    def __init__(self):
        self.transaction_exits=[]
    async def __aenter__(self): return self
    async def __aexit__(self,*args): return False
    def start_transaction(self):
        session=self
        class Transaction:
            async def __aenter__(self): return self
            async def __aexit__(self,*args):
                session.transaction_exits.append(args[0])
                return False
        return Transaction()

@pytest.fixture
def transactional_client(fake_database,monkeypatch):
    # mongomock does not implement transactions. This fixture tests transaction
    # orchestration, not Atlas rollback or commit guarantees.
    from conftest import Collection
    original=Collection.__getattr__
    def session_compatible(self,name):
        call=original(self,name)
        async def wrapped(*args,**kwargs):
            kwargs.pop("session",None)
            return await call(*args,**kwargs)
        return wrapped
    monkeypatch.setattr(Collection,"__getattr__",session_compatible)
    session=Session()
    monkeypatch.setattr(database,"client",SimpleNamespace(start_session=AsyncMock(return_value=session)))
    import combined_app
    from fastapi.testclient import TestClient
    return TestClient(combined_app.app),session


def test_delete_cascades_only_selected_meeting(transactional_client):
    client,session=transactional_client
    meeting_id=asyncio.run(save_meeting_doc({"meeting_title":"Delete test"}))
    other_id=asyncio.run(save_meeting_doc({"meeting_title":"Keep test"}))
    asyncio.run(database.collection("meeting_minutes").insert_one({"_id":meeting_id,"revisions":[{"status":"approved"}]}))
    asyncio.run(database.collection("llm_monitoring").insert_one({"meeting_id":meeting_id}))
    asyncio.run(database.collection("llm_monitoring").insert_one({"meeting_id":other_id}))
    response=client.delete("/api/meetings/"+meeting_id)
    assert response.status_code==200,response.text
    assert response.json()["status"]=="deleted"
    assert response.json()["minutes_histories_deleted"]==1
    assert response.json()["monitoring_records_deleted"]==1
    assert client.get("/api/meetings/"+meeting_id).status_code==404
    assert client.get("/api/meetings/"+other_id).status_code==200
    assert asyncio.run(database.collection("meeting_minutes").find_one({"_id":meeting_id})) is None
    assert session.transaction_exits==[None]
    assert client.delete("/api/meetings/"+meeting_id).status_code==404


def test_transaction_failure_is_not_reported_as_success(transactional_client,monkeypatch):
    client,session=transactional_client
    meeting_id=asyncio.run(save_meeting_doc({"meeting_title":"Do not falsely succeed"}))
    from conftest import Collection
    original=Collection.__getattr__
    def failing(self,name):
        if name=="delete_many":
            return AsyncMock(side_effect=RuntimeError("private-uri"))
        return original(self,name)
    monkeypatch.setattr(Collection,"__getattr__",failing)
    response=client.delete("/api/meetings/"+meeting_id)
    assert response.status_code==503
    assert "private-uri" not in response.text
    assert session.transaction_exits[-1] is RuntimeError


def test_config_uses_only_backend_dotenv(monkeypatch):
    from minutes_module import config
    called=[]
    monkeypatch.setattr(config.Path,"is_file",lambda self:True)
    monkeypatch.setattr(config,"load_dotenv",lambda path,**kwargs:called.append((path,kwargs)))
    config.Settings.load()
    assert len(called)==1
    assert called[0][0]==config.Path(config.__file__).resolve().parents[1]/".env"
    assert called[0][1]=={"override":False}
