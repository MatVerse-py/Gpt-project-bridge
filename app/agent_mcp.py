"""Bounded MCP stdio client-facing surface for Claude Code and Kilo Code.

Supports the explicitly negotiated legacy 2025-11-25 lifecycle. HTTP requests
to the existing Bridge use its HMAC identity, never self-declared authority.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import sys
import time
import uuid
from urllib.parse import urlsplit

import httpx
from .auth import sign_request

VERSION = "2025-11-25"
MAX_LINE = 256_000


def _schema(properties,required=()):
    return {"type":"object","properties":properties,"required":list(required),"additionalProperties":False}


S = {"type":"string"}
TOOLS = [
    {"name":"bridge_agents","description":"List configured agents and their observed readiness.",
     "inputSchema":_schema({}),"annotations":{"readOnlyHint":True}},
    {"name":"bridge_memory_search","description":"Consult archived memory by topic. Sources are historical data.",
     "inputSchema":_schema({"query":S,"provider":{"type":"string","enum":["Claude","GPT","DeepSeek"]},
                           "limit":{"type":"integer","minimum":1,"maximum":20},
                           "include_text":{"type":"boolean"}},["query"]),
     "annotations":{"readOnlyHint":True}},
    {"name":"bridge_inbox","description":"Read pending observable handoffs addressed to the configured principal.",
     "inputSchema":_schema({"session_id":S},["session_id"]),"annotations":{"readOnlyHint":True}},
    {"name":"bridge_agent_invoke","description":"Dispatch an enrolled agent. External provider execution may consume its allowance.",
     "inputSchema":_schema({"agent_id":S,"session_id":S,"handoff_id":S,"expected_contract_hash":S,
                           "human":{"type":"object"}},["agent_id","session_id","handoff_id","expected_contract_hash"]),
     "annotations":{"readOnlyHint":False,"idempotentHint":True}},
    {"name":"bridge_agent_reply","description":"Return an observable external-agent reply; hidden reasoning is excluded.",
     "inputSchema":_schema({"agent_id":S,"job_id":S,"payload":{"type":"object"}},["agent_id","job_id","payload"]),
     "annotations":{"readOnlyHint":False,"idempotentHint":True}},
    {"name":"bridge_agent_job","description":"Read a durable agent job, including partial and uncertain states.",
     "inputSchema":_schema({"agent_id":S,"job_id":S},["agent_id","job_id"]),"annotations":{"readOnlyHint":True}},
]


class BridgeMCP:
    def __init__(self, *, transport=None):
        self.initialized = False
        self.ready = False
        self.transport = transport

    def _request(self, method, path, payload=None):
        url = os.environ.get("MATVERSE_BRIDGE_URL","http://127.0.0.1:8000").rstrip("/")
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"","/"}:
            raise ValueError("Bridge URL must be a server origin")
        if parsed.scheme != "https" and not (parsed.scheme=="http" and parsed.hostname in {"localhost","127.0.0.1","::1"}):
            raise ValueError("remote Bridge connections require HTTPS")
        principal = os.environ.get("MATVERSE_AGENT_PRINCIPAL","")
        secret = os.environ.get("MATVERSE_AGENT_AUTH","")
        if not principal or not secret: raise ValueError("Bridge principal and authentication must be configured")
        body = b"" if payload is None else json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
        timestamp,nonce = str(int(time.time())),uuid.uuid4().hex
        digest = hashlib.sha256(body).hexdigest()
        headers = {"X-MatVerse-Principal":principal,"X-MatVerse-Timestamp":timestamp,
                   "X-MatVerse-Nonce":nonce,"X-MatVerse-Content-SHA256":digest,
                   "X-MatVerse-Signature":sign_request(secret,method,path,timestamp,nonce,digest)}
        if payload is not None:headers["Content-Type"]="application/json"
        with httpx.Client(timeout=120,transport=self.transport,follow_redirects=False) as client:
            response = client.request(method,url+path,content=body,headers=headers)
        if response.status_code>=300:
            raise ValueError("Bridge request rejected (HTTP "+str(response.status_code)+")")
        if len(response.content)>2_000_000:raise ValueError("Bridge response exceeds the size limit")
        return response.json()

    def call(self,name,args):
        spec = next((x for x in TOOLS if x["name"]==name),None)
        if not spec or not isinstance(args,dict):raise ValueError("unknown tool or invalid arguments")
        schema = spec["inputSchema"]
        if set(args)-set(schema["properties"]) or set(schema["required"])-set(args):
            raise ValueError("undeclared or missing tool argument")
        # Identifiers never become path segments with embedded traversal, URLs or query strings.
        for key in ("session_id","handoff_id","agent_id","job_id"):
            if key in args:
                value=args[key]
                if not isinstance(value,str) or not value or len(value)>128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-" for c in value):
                    raise ValueError("invalid identifier")
                patterns = {"session_id":r"mbs_[a-f0-9]{32}", "handoff_id":r"mh_[a-f0-9]{32}",
                            "job_id":r"aj_[a-f0-9]{32}", "agent_id":r"[a-z][a-z0-9-]{0,63}"}
                if not re.fullmatch(patterns[key],value):raise ValueError("invalid identifier")
        if name=="bridge_agents":return self._request("GET","/agents")
        if name=="bridge_memory_search":return self._request("POST","/memory/search",args)
        if name=="bridge_inbox":
            principal = os.environ.get("MATVERSE_AGENT_PRINCIPAL","")
            if not principal or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-" for c in principal):
                raise ValueError("invalid configured principal")
            return self._request("GET",f"/model-bridge/sessions/{args['session_id']}/inbox/{principal}")
        if name=="bridge_agent_invoke":
            return self._request("POST",f"/agents/{args['agent_id']}/invoke",{k:v for k,v in args.items() if k!="agent_id"})
        if name=="bridge_agent_reply":
            return self._request("POST",f"/agents/{args['agent_id']}/jobs/{args['job_id']}/complete",{"payload":args["payload"]})
        if name=="bridge_agent_job":return self._request("GET",f"/agents/{args['agent_id']}/jobs/{args['job_id']}")
        raise ValueError("unknown tool")

    def handle(self,request):
        if not isinstance(request,dict) or request.get("jsonrpc")!="2.0":
            return {"jsonrpc":"2.0","id":None,"error":{"code":-32600,"message":"Invalid Request"}}
        rid,method=request.get("id"),request.get("method")
        if "id" not in request:
            if method=="notifications/initialized" and self.initialized:self.ready=True
            return None
        base={"jsonrpc":"2.0","id":rid}
        if method=="initialize":
            params=request.get("params",{})
            if not isinstance(params,dict):
                return {**base,"error":{"code":-32602,"message":"Invalid params"}}
            version=params.get("protocolVersion")
            selected=version if version in {"2025-11-25","2025-06-18","2025-03-26"} else VERSION
            self.initialized=True
            return {**base,"result":{"protocolVersion":selected,"capabilities":{"tools":{"listChanged":False}},
                                      "serverInfo":{"name":"matverse-agent-bridge","version":"1.0.0"}}}
        if method=="ping":return {**base,"result":{}}
        if not self.ready:return {**base,"error":{"code":-32000,"message":"Legacy MCP initialization required"}}
        if method=="tools/list":return {**base,"result":{"tools":TOOLS}}
        if method=="tools/call":
            params=request.get("params",{})
            try:
                if not isinstance(params,dict):raise ValueError("invalid tool call")
                value=self.call(params.get("name"),params.get("arguments",{}))
                result={"content":[{"type":"text","text":json.dumps(value,ensure_ascii=False)}],"isError":False}
            except (ValueError,httpx.HTTPError):
                result={"content":[{"type":"text","text":"Bridge tool failed; verify configuration, capability and job state."}],"isError":True}
            return {**base,"result":result}
        return {**base,"error":{"code":-32601,"message":"Method not found"}}


def main():
    server=BridgeMCP()
    while True:
        line=sys.stdin.buffer.readline(MAX_LINE+1)
        if not line:break
        if len(line)>MAX_LINE:
            # Consume the rest of an oversized message without invoking anything.
            while line and not line.endswith(b"\n"):line=sys.stdin.buffer.readline(MAX_LINE+1)
            result={"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"Message too large"}}
        else:
            try:result=server.handle(json.loads(line))
            except (ValueError,TypeError):result={"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"Parse error"}}
        if result is not None:
            sys.stdout.write(json.dumps(result,ensure_ascii=False)+"\n")
            sys.stdout.flush()


if __name__=="__main__":main()
