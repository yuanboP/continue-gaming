"""Shared structured decision schema; reported coordinates are audit-only."""
from __future__ import annotations

import hashlib
import json


def schema_hash(schema: dict | None) -> str | None:
    if schema is None:
        return None
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def grounded_schema(max_chunks: int = 3, max_duration: int = 48) -> dict:
    """Named action objects avoid ambiguity in fixed-width action arrays.

    Coordinates are the model's own visual estimates and must never feed an
    automatic controller. A null player represents an absent/dead player.
    """
    if type(max_chunks) is not int or not 1 <= max_chunks <= 4:
        raise ValueError('max_chunks must be an integer in 1..4')
    if type(max_duration) is not int or not 1 <= max_duration <= 120:
        raise ValueError('max_duration must be an integer in 1..120')
    def point():
        return {'type': 'array', 'items': {'type': 'number'}, 'minItems': 2, 'maxItems': 2}
    action = {'type': 'object', 'additionalProperties': False,
              'required': ['move', 'shoot', 'focus', 'bomb', 'duration'],
              'properties': {
                  'move': {'type': 'integer', 'enum': list(range(9))},
                  'shoot': {'type': 'integer', 'enum': [0, 1]},
                  'focus': {'type': 'integer', 'enum': [0, 1]},
                  'bomb': {'type': 'integer', 'enum': [0, 1]},
                  'duration': {'type': 'integer', 'minimum': 1, 'maximum': max_duration}}}
    return {'type': 'object', 'additionalProperties': False,
            'required': ['player', 'boss', 'aim_x', 'plan'],
            'properties': {
                'player': {'anyOf': [point(), {'type': 'null'}]},
                'boss': point(), 'aim_x': {'type': 'number'},
                'plan': {'type': 'array', 'minItems': 1, 'maxItems': max_chunks, 'items': action}}}


FORECAST_CANDIDATES = ()


def decision_schema(candidate: str, max_chunks: int = 3, max_duration: int = 48) -> dict:
    """Model forecast fields are audit-only; executor still consumes only plan."""
    schema = grounded_schema(max_chunks, max_duration)
    if candidate not in FORECAST_CANDIDATES:
        return schema
    point = {'type':'array','items':{'type':'number'},'minItems':2,'maxItems':2}
    nullable_point = {'anyOf':[point,{'type':'null'}]}
    forecast = {'type':'object','additionalProperties':False,
        'required':['boss_vx_px_s','player_at_apply','endpoint','boss_at_shot_x'],
        'properties':{'boss_vx_px_s':{'type':'number'},
            'player_at_apply':nullable_point,'endpoint':nullable_point,
            'boss_at_shot_x':{'type':'number'}}}
    plan = schema['properties'].pop('plan')
    schema['properties'].update(forecast=forecast, plan=plan)
    schema['required'].insert(-1,'forecast')
    return schema


def resolve_schema_order(candidate, override=None):
    """None means historical candidate default; explicit overrides must be bool."""
    if override is not None and type(override) is not bool:
        raise ValueError('preserve_schema_order must be boolean')
    return candidate in FORECAST_CANDIDATES if override is None else override


def transmitted_schema_hash(schema, preserve):
    return hashlib.sha256(json.dumps(schema,sort_keys=not preserve,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def validate_schema_order_config(config, schema=None):
    if 'preserve_schema_order' in config and type(config['preserve_schema_order']) is not bool:
        raise ValueError('preserve_schema_order must be boolean')
    if 'preserve_schema_order' in config and config.get('structured') is not True:
        raise ValueError('Explicit schema order requires structured output')
    preserve=resolve_schema_order(config.get('candidate',''),config.get('preserve_schema_order'))
    if schema is None and config.get('structured'):
        schema=decision_schema(config.get('candidate',''),config.get('chunk_size',1),config.get('max_duration',48))
        if config.get('decision_brief'):
            from .live_summary import add_brief_schema
            schema=add_brief_schema(schema)
    if 'schema_order_sha256' in config and config['schema_order_sha256']!=transmitted_schema_hash(schema,preserve):
        raise ValueError('Schema property order differs from frozen configuration')
    if 'schema_sha256' in config and config['schema_sha256']!=schema_hash(schema):
        raise ValueError('Schema differs from frozen configuration')
    return preserve
