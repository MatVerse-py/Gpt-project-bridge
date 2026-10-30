#!/usr/bin/env python3
"""MatVerse Witness Engine - read-only MCP stdio bridge (stdlib-only).

This adapter does not certify authorship, scientific validity, wallet ownership,
or proof of document anchoring. It never writes to third-party providers.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

API_BASE = 'https://matverse-witness-engine-iu685x.v2.appdeploy.ai'
MCP_VERSION = '2024-11-05'
MAX_LINE = 256_000
MAX_OUTPUT = 950_000

TOOLS = [
    {
        'name': 'witness_health',
        'description': 'Ler status publico do motor. Nao valida autoria nem ciencia.',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}
    },
    {
        'name': 'witness_get_snapshot',
        'description': 'Ler ultimo snapshot diario disponivel de metadados e respectivos HOLDs.',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}
    },
    {
        'name': 'witness_scan_public',
        'description': 'Consultar conjunto FIXO e limitado de registros publicos Crossref/Zenodo, sem escrita.',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}
    },
    {
        'name': 'witness_evaluate_claim',
        'description': 'Simular Psi e CVaR com valores fornecidos, regras de bloqueio e receipt de demonstracao; NAO prova verdade.',
        'inputSchema': {
            'type': 'object',
            'required': ['claim', 'metrics', 'losses'],
            'additionalProperties': False,
            'properties': {
                'claim': {'type': 'string', 'minLength': 1, 'maxLength': 3000},
                'metrics': {
                    'type': 'object',
                    'required': ['completeness', 'consistency', 'traceability'],
                    'properties': {
                        'completeness': {'type': 'number', 'minimum': 0, 'maximum': 1},
                        'consistency': {'type': 'number', 'minimum': 0, 'maximum': 1},
                        'traceability': {'type': 'number', 'minimum': 0, 'maximum': 1}
                    }
                },
                'losses': {'type': 'array', 'items': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'minItems': 2, 'maxItems': 500},
                'alpha': {'type': 'number', 'exclusiveMinimum': 0, 'exclusiveMaximum': 1, 'default': 0.95},
                'sources': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 20},
                'criticalContradiction': {'type': 'boolean', 'default': False}
            }
        }
    },
    {
        'name': 'witness_integrations',
        'description': 'Listar links de exploradores Amoy/Sepolia e aplicativos MatVerse existentes; provas documentais em HOLD.',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}
    }
]
ROUTES = {
    'witness_health': ('GET', '/api/health'),
    'witness_get_snapshot': ('GET', '/api/snapshot'),
    'witness_scan_public': ('POST', '/api/scan'),
    'witness_evaluate_claim': ('POST', '/api/evaluate'),
    'witness_integrations': ('GET', '/api/integrations')
}


def call_remote(name: str, params: dict[str, Any]) -> Any:
    if name not in ROUTES:
        raise ValueError('UNKNOWN_TOOL')
    method, path = ROUTES[name]
    body = None if method == 'GET' else json.dumps(params, ensure_ascii=False).encode('utf-8')
    if body is not None and len(body) > 65_536:
        raise ValueError('PAYLOAD_TOO_LARGE')
    request = urllib.request.Request(
        API_BASE + path,
        data=body,
        headers={'Accept': 'application/json',
                 'Content-Type': 'application/json',
                 'User-Agent': 'MatVerse-Witness-MCP/0.1'},
        method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=24) as response:
            raw = response.read(MAX_OUTPUT + 1)
            if len(raw) > MAX_OUTPUT:
                raise ValueError('RESPONSE_TOO_LARGE')
            return json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise RuntimeError('REMOTE_HTTP_' + str(exc.code)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError('REMOTE_UNAVAILABLE') from exc


def result_for(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get('method')
    identifier = request.get('id')
    if method == 'notifications/initialized' or method == 'notifications/cancelled':
        return None
    try:
        if method == 'initialize':
            requested = (request.get('params') or {}).get('protocolVersion')
            version = requested if requested in ('2024-11-05', '2025-03-26', '2025-06-18') else MCP_VERSION
            result = {
                'protocolVersion': version,
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': 'matverse-witness-mcp', 'version': '0.1.0'},
                'instructions': 'Somente leitura de metadados. Nunca tratar recibos de demonstracao como prova de autoria ou validade cientifica.'
            }
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': TOOLS}
        elif method == 'tools/call':
            params = request.get('params') or {}
            name = params.get('name')
            arguments = params.get('arguments') or {}
            if name not in ROUTES:
                raise ValueError('UNKNOWN_TOOL')
            if not isinstance(arguments, dict):
                raise ValueError('INVALID_ARGUMENTS')
            if name != 'witness_evaluate_claim' and arguments:
                raise ValueError('UNEXPECTED_ARGUMENTS')
            data = call_remote(name, arguments)
            result = {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}], 'isError': False}
        else:
            raise ValueError('METHOD_NOT_FOUND')
        return {'jsonrpc': '2.0', 'id': identifier, 'result': result}
    except Exception as exc:
        msg = str(exc)
        if method == 'tools/call' and identifier is not None:
            return {'jsonrpc': '2.0', 'id': identifier, 'result': {
                'content': [{'type': 'text', 'text': json.dumps({'status': 'HOLD', 'error': msg[:160]})}],
                'isError': True}}
        return {'jsonrpc': '2.0', 'id': identifier, 'error': {'code': -32601 if msg == 'METHOD_NOT_FOUND' else -32602, 'message': msg[:160]}}


def serve() -> None:
    for line in sys.stdin.buffer:
        if len(line) > MAX_LINE:
            sys.stderr.write('MCP message too large; rejected\n')
            continue
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError('Expected JSON-RPC object')
            response = result_for(request)
            if response is not None and request.get('id') is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(',', ':')) + '\n')
                sys.stdout.flush()
        except Exception as exc:
            sys.stderr.write('MCP input error: ' + type(exc).__name__ + '\n')


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        assert all(x['name'] in ROUTES for x in TOOLS)
        init = result_for({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
        assert init is not None and init['result']['serverInfo']['name'] == 'matverse-witness-mcp'
        listed = result_for({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        assert listed is not None and len(listed['result']['tools']) == 5
        no_net = result_for({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'unknown'}})
        assert no_net is not None and no_net['result']['isError']
        print('3/3 MCP smoke tests passed (local; no upstream verification)')
    else:
        serve()
