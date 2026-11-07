"""Motor de transformações determinísticas com reator de feedback discreto.

Sem execução de código arbitrário, subprocessos ou efeitos sobre dispositivos.
Camada de autorização constitucional permanece EXTERNA ao motor.
"""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping
from types import MappingProxyType

SCHEMA = "matverse.reactor-graph.v1"
KINDS = frozenset({"TRANSDUCER", "MOTOR", "REACTOR", "DISSIPATOR", "DIFFUSER"})
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
MAX_NODES = 64
MAX_MAGNITUDE = 1e9


def canon(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(data: Any) -> str:
    return sha256(canon(data).encode('utf-8')).hexdigest()


def finite(value: Any, label: str = 'value') -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be a finite numeric scalar')
    n = float(value)
    if not math.isfinite(n) or abs(n) > MAX_MAGNITUDE:
        raise ValueError(f'{label} outside finite bound')
    return n


@dataclass(frozen=True)
class Operator:
    node_id: str
    kind: str
    source: str | None = None
    params: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not ID_RE.fullmatch(self.node_id):
            raise ValueError('Invalid node_id')
        if self.kind not in KINDS:
            raise ValueError('Unknown operator kind')
        if self.params is not None and not isinstance(self.params, Mapping):
            raise ValueError('params must be object')
        if self.kind != 'TRANSDUCER' and self.source is None:
            raise ValueError('non-transducer node requires source node')
        if self.kind == 'TRANSDUCER' and self.source is not None:
            raise ValueError('transducer source belongs to params.field')


class ReactorGraph:
    def __init__(self, operators: list[Operator]):
        if not operators or len(operators) > MAX_NODES:
            raise ValueError('Graph must contain 1..64 operators')
        ids = [n.node_id for n in operators]
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate node identity')
        registry = {n.node_id:Operator(n.node_id,n.kind,n.source,MappingProxyType(dict(n.params or {}))) for n in operators}
        for node in operators:
            if node.source and node.source not in registry:
                raise ValueError(f'Unknown source for {node.node_id}')
            if node.source == node.node_id:
                raise ValueError('Self-dependency not permitted')
            p = node.params or {}
            allowed = {
                'TRANSDUCER':{'field','scale','offset'},
                'MOTOR':{'gain'},
                'REACTOR':{'feedback'},
                'DISSIPATOR':{'minimum','maximum'},
                'DIFFUSER':{'ratio'},
            }[node.kind]
            if set(p) - allowed:
                raise ValueError(f'Unsupported parameters for {node.kind}')
            if node.kind == 'TRANSDUCER':
                if not isinstance(p.get('field'), str) or not ID_RE.fullmatch(p['field']):
                    raise ValueError('Invalid transducer input field')
                finite(p.get('scale',1.0),'scale');finite(p.get('offset',0.0),'offset')
            elif node.kind == 'MOTOR':
                finite(p.get('gain',1.0),'gain')
            elif node.kind == 'REACTOR':
                f=finite(p.get('feedback',0.25),'feedback')
                if not 0 <= f < 1:raise ValueError('feedback must be in [0,1)')
            elif node.kind == 'DISSIPATOR':
                a,b=finite(p.get('minimum',0.),'minimum'),finite(p.get('maximum',1.),'maximum')
                if a>b:raise ValueError('minimum > maximum')
            elif node.kind == 'DIFFUSER':
                r=finite(p.get('ratio',0.5),'ratio')
                if not 0<=r<=1:raise ValueError('ratio must be [0,1]')

        visited:set[str]=set();active:set[str]=set();sorted_nodes:list[Operator]=[]
        def visit(identifier:str) -> None:
            if identifier in active:raise ValueError('Graph cycle rejected; feedback uses prior cycle state')
            if identifier in visited:return
            active.add(identifier)
            op=registry[identifier]
            if op.source:visit(op.source)
            active.remove(identifier);visited.add(identifier);sorted_nodes.append(op)
        for i in sorted(ids):visit(i)
        # DIFFUSER is terminal: it yields two channels, not a scalar.
        if any(n.source and registry[n.source].kind=='DIFFUSER' for n in sorted_nodes):
            raise ValueError('DIFFUSER cannot feed scalar operator')
        self.nodes=tuple(sorted_nodes)
        self.spec=[{'node_id':n.node_id,'kind':n.kind,'source':n.source,
                    'params':dict(n.params or {})} for n in self.nodes]
        self.fingerprint=digest({'schema':SCHEMA,'nodes':self.spec})

    @classmethod
    def from_dict(cls, data: Mapping[str,Any]) -> 'ReactorGraph':
        if not isinstance(data,dict) or data.get('schema')!=SCHEMA:
            raise ValueError('Graph schema mismatch')
        rows=data.get('nodes')
        if not isinstance(rows,list):raise ValueError('nodes must be array')
        if any(not isinstance(row,dict) or set(row)-{'node_id','kind','source','params'} for row in rows):
            raise ValueError('Invalid graph node object')
        return cls([Operator(**row) for row in rows])

    def run(self, data: Mapping[str,Any], previous: Mapping[str,float] | None = None,
            *, evidence_class:str='USER_SUPPLIED') -> dict[str,Any]:
        if evidence_class not in {'USER_SUPPLIED','SIMULATED','MEASURED_LOCAL'}:
            raise ValueError('Unknown evidence class')
        if not isinstance(data,dict) or not isinstance(previous,(dict,type(None))):
            raise ValueError('Inputs and previous state must be dictionaries')
        values:dict[str,Any]={};state:dict[str,float]={}
        for node in self.nodes:
            p=node.params or {}
            if node.kind=='TRANSDUCER':
                key=p['field']
                if key not in data:raise ValueError(f'Missing input field for {node.node_id}')
                value=finite(data[key],key)*finite(p.get('scale',1))+finite(p.get('offset',0))
            else:
                x=values[node.source]
                if not isinstance(x,float):raise ValueError('Non-scalar input')
                if node.kind=='MOTOR':value=x*finite(p.get('gain',1))
                elif node.kind=='REACTOR':
                    old=finite(previous.get(node.node_id,x) if previous else x,'previous')
                    w=finite(p.get('feedback',0.25))
                    value=(1-w)*x+w*old
                    state[node.node_id]=finite(value)
                elif node.kind=='DISSIPATOR':value=min(max(x,finite(p.get('minimum',0))),finite(p.get('maximum',1)))
                else:
                    ratio=finite(p.get('ratio',0.5))
                    values[node.node_id]={'channel_a':finite(x*ratio),'channel_b':finite(x*(1-ratio))}
                    continue
            values[node.node_id]=finite(value)
        body={'schema':'matverse.reactor-result.v1','graph_fingerprint':self.fingerprint,
              'evidence_class':evidence_class,'values':values,'next_state':state,
              'execution_permission':False,'external_effects':[],
              'authorization':'HOLD_EXTERNAL_GATE'}
        return {**body,'result_hash':digest(body)}
