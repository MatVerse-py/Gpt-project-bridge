from __future__ import annotations
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app import storage, agent_router
from app.agent_runtime import AgentBroker, AgentConfigurationError, AgentProviderError, AgentSettings, agent_catalog
from app.agent_jobs import AgentConflict, claim_job, finish_job, get_job
from app.agent_mcp import BridgeMCP
from app.memory_router import MemoryQuery, search_memory
from conftest import auth_request

SECRET = "sk-unit-fixture-credential-unique-value"
PAYLOAD = {"kind":"agent_task","public_summary":"Return an observable result.","state":{"probe":"test"}}


def settings(monkeypatch,agent="claude"):
    env = {
        "ANTHROPIC_MODEL":"claude-unit-model","ANTHROPIC_API_KEY":SECRET,
        "KILO_MODEL":"qwen/unit-model:free","KILO_ALLOW_ANONYMOUS":"true",
        "MANUS_API_KEY":SECRET,"MANUS_AGENT_PROFILE":"lite",
        "BASE44_ACCESS_TOKEN":SECRET,"BASE44_APP_ID":"0123456789abcdef01234567",
        "CLAUDE_WEB_MODEL":"claude-ui-test",
    }
    for k,v in env.items():monkeypatch.setenv(k,v)
    return AgentSettings.from_env(agent)


def claude_response(text="public result",stop="end_turn"):
    return {"id":"msg_unit","model":"claude-unit-model","stop_reason":stop,
            "content":[{"type":"thinking","thinking":"private reasoning fixture"},
                       {"type":"text","text":text}],"usage":{"input_tokens":4,"output_tokens":2}}


def session(client,agent="claude",model="claude-unit-model",provider="anthropic"):
    hashes = {}
    for kind in ("ontology","policy","task","rubric","memory_policy"):
        r=auth_request(client,"admin","POST","/registry/contracts",
                       {"kind":kind,"version":"agent-unit-test","content":{"kind":kind}})
        assert r.status_code==200
        hashes[kind+"_hash"]=r.json()["artifact_hash"]
    r=auth_request(client,"admin","POST","/model-bridge/sessions",
       {"participants":[{"participant_id":"gpt","provider":"openai","model":"gpt-unit"},
                        {"participant_id":agent,"provider":provider,"model":model}],"contract":hashes})
    assert r.status_code==200,r.text
    s=r.json()
    r=auth_request(client,"admin","POST",f"/model-bridge/sessions/{s['session_id']}/handoffs",
       {"from_participant":"gpt","to_participant":agent,"expected_contract_hash":s["contract_hash"],"payload":PAYLOAD})
    assert r.status_code==200,r.text
    handoff=r.json().get("handoff",r.json())
    return {"session_id":s["session_id"],"handoff_id":handoff["handoff_id"],"expected_contract_hash":s["contract_hash"]}


def install_transport(monkeypatch,handler):
    original=AgentBroker
    monkeypatch.setattr(agent_router,"AgentBroker",lambda s,actor:original(s,actor=actor,transport=httpx.MockTransport(handler)))


def test_anthropic_real_wire_contract_and_private_reasoning_boundary(monkeypatch):
    s=settings(monkeypatch)
    seen=[]
    def handler(req):
        seen.append(req)
        assert str(req.url)=="https://api.anthropic.com/v1/messages"
        assert req.headers["Authorization"]=="Bearer "+SECRET
        assert req.headers["anthropic-version"]=="2023-06-01"
        body=json.loads(req.content)
        assert body["model"]==s.model and body["max_tokens"]==1024
        assert "system" not in body
        return httpx.Response(200,json=claude_response())
    broker=AgentBroker(s,actor="operator",transport=httpx.MockTransport(handler))
    result=broker.invoke(PAYLOAD)
    assert result["output_text"]=="public result" and result["status"]=="COMPLETE"
    public=json.dumps({"result":result,"audit":broker.plane.audit_events()})
    assert SECRET not in public and "private reasoning fixture" not in public
    assert len(seen)==1


def test_human_boundary_blocks_before_credential_read(monkeypatch):
    s=settings(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    broker=AgentBroker(s,actor="operator")
    result=broker.invoke(PAYLOAD,{"sensitivity":"SECRET","consent":True,"purpose":"test"})
    assert result["decision"]=="BLOCK" and result["executed"] is False
    assert not any(x["event_type"]=="LEASE_ISSUED" for x in broker.plane.audit_events())


@pytest.mark.parametrize("field",["api_key","privateMemory","chainOfThought","system_prompt"])
def test_hidden_fields_never_reach_transport(monkeypatch,field):
    broker=AgentBroker(settings(monkeypatch),actor="operator")
    with pytest.raises(ValueError):broker.invoke({"state":{field:"must remain local"}})
    assert not any(x["event_type"]=="LEASE_ISSUED" for x in broker.plane.audit_events())


def test_kilo_anonymous_is_explicit_and_free_only(monkeypatch):
    s=settings(monkeypatch,"kilo")
    def handler(req):
        assert "Authorization" not in req.headers
        assert req.url.path=="/api/gateway/chat/completions"
        assert json.loads(req.content)["model"].endswith(":free")
        return httpx.Response(200,json={"id":"k","model":"qwen/unit-model","choices":[
            {"message":{"content":"ok"},"finish_reason":"stop"}]})
    result=AgentBroker(s,actor="operator",transport=httpx.MockTransport(handler)).invoke(PAYLOAD)
    assert result["status"]=="COMPLETE"
    monkeypatch.setenv("KILO_MODEL","paid/model")
    with pytest.raises(AgentConfigurationError):AgentSettings.from_env("kilo")


@pytest.mark.parametrize("response",[httpx.Response(302,headers={"Location":"https://example.org"}),
    httpx.Response(429,text="sensitive provider error"),httpx.Response(200,text="not json"),
    httpx.Response(200,json={"content":[{"type":"thinking","thinking":"private"}]})])
def test_provider_failures_are_sanitized(monkeypatch,response):
    broker=AgentBroker(settings(monkeypatch),actor="operator",transport=httpx.MockTransport(lambda _:response))
    with pytest.raises(AgentProviderError) as exc:broker.invoke(PAYLOAD)
    assert SECRET not in str(exc.value) and "sensitive provider error" not in str(exc.value)


def test_truncated_generation_is_partial(monkeypatch):
    broker=AgentBroker(settings(monkeypatch),actor="operator",
        transport=httpx.MockTransport(lambda _:httpx.Response(200,json=claude_response(stop="max_tokens"))))
    assert broker.invoke(PAYLOAD)["status"]=="PARTIAL"


def test_response_echoing_provider_secret_is_blocked(monkeypatch):
    from app.secret_plane import SecretExposureError
    broker=AgentBroker(settings(monkeypatch),actor="operator",
        transport=httpx.MockTransport(lambda _:httpx.Response(200,json=claude_response(SECRET))))
    with pytest.raises(SecretExposureError):broker.invoke(PAYLOAD)


def test_manus_v2_creation_is_async_and_private(monkeypatch):
    s=settings(monkeypatch,"manus")
    def handler(req):
        assert req.url.path=="/v2/task.create"
        assert req.headers["x-manus-api-key"]==SECRET
        b=json.loads(req.content)
        assert b["share_visibility"]=="private" and b["agent_profile"]=="lite"
        assert b["message"]["connectors"]==[]
        return httpx.Response(200,json={"ok":True,"task_id":"task_unit","request_id":"r"})
    result=AgentBroker(s,actor="operator",transport=httpx.MockTransport(handler)).invoke(PAYLOAD)
    assert result["status"]=="RUNNING" and "output_text" not in result


@pytest.mark.parametrize("background",[True,None])
def test_manus_stopped_does_not_imply_completion(monkeypatch,background):
    task={"id":"task_unit","status":"stopped"}
    if background is not None:task["has_running_background_jobs"]=background
    broker=AgentBroker(settings(monkeypatch,"manus"),actor="operator",
        transport=httpx.MockTransport(lambda _:httpx.Response(200,json={"ok":True,"task":task})))
    assert broker.poll_manus("task_unit")["status"]=="RUNNING"


def test_manus_final_output_excludes_internal_events(monkeypatch):
    def handler(req):
        if req.url.path.endswith("task.detail"):
            return httpx.Response(200,json={"ok":True,"task":{"id":"task_unit","status":"stopped","has_running_background_jobs":False}})
        assert req.url.params["verbose"]=="false"
        return httpx.Response(200,json={"ok":True,"messages":[
            {"type":"explanation","explanation":{"content":"private thought"}},
            {"type":"assistant_message","assistant_message":{"content":"final result","delivery_kind":"result"}}]})
    broker=AgentBroker(settings(monkeypatch,"manus"),actor="operator",transport=httpx.MockTransport(handler))
    assert broker.poll_manus("task_unit")["output_text"]=="final result"


def test_base44_mcp_session_and_app_binding(monkeypatch):
    seen=[]
    def handler(req):
        body=json.loads(req.content);seen.append(body["method"])
        assert req.headers["Authorization"]=="Bearer "+SECRET
        if body["method"]=="initialize":
            return httpx.Response(200,headers={"Mcp-Session-Id":"session_unit"},json={"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-11-25"}})
        assert req.headers["Mcp-Session-Id"]=="session_unit"
        if body["method"]=="notifications/initialized":return httpx.Response(202)
        assert body["params"]["name"]=="list_directory"
        assert body["params"]["arguments"]["appId"]=="0123456789abcdef01234567"
        return httpx.Response(200,json={"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"files"}]}})
    broker=AgentBroker(settings(monkeypatch,"base44"),actor="operator",transport=httpx.MockTransport(handler))
    r=broker.invoke({"state":{"tool":"list_directory","arguments":{}}})
    assert r["status"]=="COMPLETE" and len(seen)==3


@pytest.mark.parametrize("state",[
    {"tool":"run_command","arguments":{"command":"anything"}},
    {"tool":"read_file","arguments":{"path":".agents/.env"}},
    {"tool":"read_file","arguments":{"path":"../private"}},
    {"tool":"list_directory","arguments":{"appId":"aaaaaaaaaaaaaaaaaaaaaaaa"}},
    {"tool":"write_file","arguments":{"path":"src/new.js","content":"x"}},
])
def test_base44_unauthorized_tools_paths_or_apps_never_call_http(monkeypatch,state):
    seen=[]
    broker=AgentBroker(settings(monkeypatch,"base44"),actor="operator",
        transport=httpx.MockTransport(lambda r:seen.append(r)))
    with pytest.raises((ValueError,PermissionError)):broker.invoke({"state":state})
    assert seen==[]


def test_full_bridge_http_reply_is_atomic_and_retry_idempotent(client,monkeypatch):
    settings(monkeypatch)
    calls=[]
    install_transport(monkeypatch,lambda req:(calls.append(req) or httpx.Response(200,json=claude_response())))
    req=session(client)
    first=auth_request(client,"admin","POST","/agents/claude/invoke",req)
    assert first.status_code==200,first.text
    job=first.json()
    assert job["status"]=="COMPLETE" and len(calls)==1
    second=auth_request(client,"admin","POST","/agents/claude/invoke",req)
    assert second.json()["job_id"]==job["job_id"] and len(calls)==1
    assert storage.list_model_inbox(req["session_id"],"claude")==[]
    replies=storage.list_model_inbox(req["session_id"],"gpt")
    assert len(replies)==1 and replies[0]["payload"]["public_summary"]=="public result"
    assert storage.verify_chain()["ok"]


def test_uncertain_timeout_cannot_silently_redispatch(client,monkeypatch):
    settings(monkeypatch)
    calls=[]
    def timeout(req):
        calls.append(req);raise httpx.ReadTimeout("fixture",request=req)
    install_transport(monkeypatch,timeout)
    req=session(client)
    assert auth_request(client,"admin","POST","/agents/claude/invoke",req).status_code==502
    r=auth_request(client,"admin","POST","/agents/claude/invoke",req)
    assert r.status_code==200 and r.json()["status"]=="UNCERTAIN" and len(calls)==1
    assert len(storage.list_model_inbox(req["session_id"],"claude"))==1


def test_agent_capabilities_and_enrollment_are_enforced(client,monkeypatch):
    settings(monkeypatch)
    req=session(client)
    assert auth_request(client,"gpt","POST","/agents/claude/invoke",req).status_code==403
    req["expected_contract_hash"]="0"*64
    assert auth_request(client,"admin","POST","/agents/claude/invoke",req).status_code==403


def test_configuration_drift_does_not_execute(client,monkeypatch):
    settings(monkeypatch)
    req=session(client,model="different-model")
    assert auth_request(client,"admin","POST","/agents/claude/invoke",req).status_code==403


def test_external_claude_reply_and_source_ack(client,monkeypatch):
    settings(monkeypatch)
    req=session(client,agent="claude-web",model="claude-ui-test")
    r=auth_request(client,"admin","POST","/agents/claude-web/invoke",req)
    assert r.status_code==200,r.text
    assert r.json()["status"]=="WAITING_EXTERNAL" and "dispatch_payload" in r.json()
    job_id=r.json()["job_id"]
    payload={"kind":"agent_reply","public_summary":"external visible result"}
    done=auth_request(client,"admin","POST",f"/agents/claude-web/jobs/{job_id}/complete",{"payload":payload})
    assert done.status_code==200,done.text
    assert done.json()["result"]["provenance"]=="authenticated_external_client"
    conflict=auth_request(client,"admin","POST",f"/agents/claude-web/jobs/{job_id}/complete",
                          {"payload":{"public_summary":"changed reply"}})
    assert conflict.status_code==409


def test_parallel_dispatch_claim_has_one_winner(client):
    req=session(client)
    kwargs={"agent_id":"claude","session_id":req["session_id"],"handoff_id":req["handoff_id"],
            "contract_hash":req["expected_contract_hash"],"actor":"operator","transport":"anthropic_messages",
            "model":"claude-unit-model"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:claim_job(**kwargs),range(4)))
    assert sum(claimed for _,claimed in results)==1


def test_mcp_stdio_lifecycle_and_wire_signing(monkeypatch):
    monkeypatch.setenv("MATVERSE_AGENT_PRINCIPAL","unit-agent")
    monkeypatch.setenv("MATVERSE_AGENT_AUTH","unit-principal-secret")
    def handler(req):
        assert req.headers["X-MatVerse-Principal"]=="unit-agent"
        assert req.headers["X-MatVerse-Signature"]
        return httpx.Response(200,json={"agents":[]})
    server=BridgeMCP(transport=httpx.MockTransport(handler))
    assert "error" in server.handle({"jsonrpc":"2.0","id":1,"method":"tools/list"})
    server.handle({"jsonrpc":"2.0","id":2,"method":"initialize","params":{"protocolVersion":"2025-11-25"}})
    server.handle({"jsonrpc":"2.0","method":"notifications/initialized"})
    result=server.handle({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"bridge_agents","arguments":{}}})
    assert result["result"]["isError"] is False
    with pytest.raises(ValueError):server.call("bridge_agent_job",{"agent_id":"../private","job_id":"aj_x"})


def test_mcp_rejects_plain_http_remote_origin(monkeypatch):
    monkeypatch.setenv("MATVERSE_BRIDGE_URL","http://example.org")
    with pytest.raises(ValueError,match="HTTPS"):BridgeMCP().call("bridge_agents",{})


def test_memory_read_only_excludes_thinking_and_redacts_credentials(tmp_path,monkeypatch):
    p=tmp_path/"memory.db"
    conn=sqlite3.connect(p)
    conn.execute("create table records(id integer primary key,provider,kind,role,title,timestamp,conversation_uuid,message_uuid,locator,body)")
    conn.execute("create table search_chunks(id integer primary key,record_id)")
    conn.execute("create virtual table search using fts5(body)")
    for rid,kind,body in [(1,"message","Cassandra public sk-redact-this-secret-123456789"),(2,"content_block_thinking","Cassandra private thought")]:
        conn.execute("insert into records values(?,?,?,?,?,?,?,?,?,?)",(rid,"Claude",kind,"assistant","test","","c","m","source",body))
        conn.execute("insert into search_chunks values(?,?)",(rid,rid))
        conn.execute("insert into search(rowid,body) values(?,?)",(rid,body))
    conn.commit();conn.close()
    monkeypatch.setenv("MATVERSE_MEMORY_DB",str(p))
    hits=search_memory(MemoryQuery(query="Cassandra",include_text=True))
    assert len(hits)==1 and hits[0]["id"]==1
    assert "sk-redact" not in hits[0]["excerpt"]
    assert "excerpt" not in search_memory(MemoryQuery(query="Cassandra"))[0]
    assert sqlite3.connect(p).execute("select count(*) from records").fetchone()[0]==2


@pytest.mark.parametrize("provider_status,expected",[("completed","COMPLETE"),("incomplete","PARTIAL"),(None,"UNKNOWN")])
def test_gpt_completion_requires_provider_status_and_uses_agent_budget(monkeypatch,provider_status,expected):
    monkeypatch.setenv("OPENAI_API_KEY",SECRET)
    monkeypatch.setenv("OPENAI_MODEL","gpt-unit")
    monkeypatch.setenv("MATVERSE_AGENT_MAX_OUTPUT_TOKENS","256")
    def handler(req):
        assert json.loads(req.content)["max_output_tokens"]==256
        data={"id":"resp_unit","model":"gpt-unit","output":[{"type":"message","role":"assistant",
              "content":[{"type":"output_text","text":"public result"}]}]}
        if provider_status:data["status"]=provider_status
        return httpx.Response(200,json=data)
    broker=AgentBroker(AgentSettings.from_env("gpt"),actor="operator",transport=httpx.MockTransport(handler))
    assert broker.invoke(PAYLOAD)["status"]==expected


def test_manus_async_bridge_does_not_ack_until_final_output(client,monkeypatch):
    settings(monkeypatch,"manus")
    background=[True,False]
    def handler(req):
        if req.url.path.endswith("task.create"):
            return httpx.Response(200,json={"ok":True,"task_id":"task_unit"})
        if req.url.path.endswith("task.detail"):
            return httpx.Response(200,json={"ok":True,"task":{"id":"task_unit","status":"stopped",
                                   "has_running_background_jobs":background.pop(0)}})
        return httpx.Response(200,json={"ok":True,"task_id":"task_unit","messages":[
            {"type":"assistant_message","assistant_message":{"delivery_kind":"result","content":"finished work"}}]})
    install_transport(monkeypatch,handler)
    req=session(client,agent="manus",model="lite",provider="manus")
    first=auth_request(client,"admin","POST","/agents/manus/invoke",req).json()
    assert first["status"]=="RUNNING"
    path=f"/agents/manus/jobs/{first['job_id']}/poll"
    assert auth_request(client,"admin","POST",path).json()["status"]=="RUNNING"
    assert len(storage.list_model_inbox(req["session_id"],"manus"))==1
    assert storage.list_model_inbox(req["session_id"],"gpt")==[]
    done=auth_request(client,"admin","POST",path)
    assert done.status_code==200 and done.json()["status"]=="COMPLETE"
    assert storage.list_model_inbox(req["session_id"],"manus")==[]
    assert storage.list_model_inbox(req["session_id"],"gpt")[0]["payload"]["public_summary"]=="finished work"
    assert storage.verify_chain()["ok"]


@pytest.mark.parametrize("messages",[[None],[{"type":"assistant_message","assistant_message":None}]])
def test_manus_malformed_public_messages_fail_closed(monkeypatch,messages):
    def handler(req):
        if req.url.path.endswith("task.detail"):
            return httpx.Response(200,json={"ok":True,"task":{"id":"task_unit","status":"stopped","has_running_background_jobs":False}})
        return httpx.Response(200,json={"ok":True,"messages":messages})
    broker=AgentBroker(settings(monkeypatch,"manus"),actor="operator",transport=httpx.MockTransport(handler))
    with pytest.raises(AgentProviderError,match="manus_invalid_messages"):broker.poll_manus("task_unit")


def test_expired_external_reply_cannot_ack_the_input(client,monkeypatch):
    settings(monkeypatch)
    req=session(client,agent="claude-web",model="claude-ui-test")
    job=auth_request(client,"admin","POST","/agents/claude-web/invoke",req).json()
    import app.agent_jobs as jobs
    monkeypatch.setattr(jobs.time,"time",lambda:job["deadline"]+1)
    with pytest.raises(AgentConflict,match="expired"):
        finish_job(job["job_id"],{"public_summary":"too late"},"authenticated_external_client")
    assert len(storage.list_model_inbox(req["session_id"],"claude-web"))==1
    assert get_job(job["job_id"])["status"]=="WAITING_EXTERNAL"


def test_mcp_rejects_dot_path_segments_before_network():
    with pytest.raises(ValueError,match="identifier"):
        BridgeMCP().call("bridge_agent_job",{"agent_id":"..","job_id":"aj_"+"a"*32})
