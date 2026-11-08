"""Bounded, portable workflow recipes; no jobs, credentials or capture results."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
import tempfile

from .bracket import WorkflowError, normalize_plan

FORMAT = 'hasslefree.workflow-recipe.v1'
MAX_RECIPE_BYTES = 65536


class RecipeError(WorkflowError):
    """The recipe is invalid or could not be read/written safely."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecipeError('duplicate JSON keys are forbidden')
        result[key] = value
    return result


def _reject_constant(value):
    raise RecipeError('nonfinite JSON values are forbidden')


def _json_integer(value):
    # JSON has one number type: integral decimal/exponent forms normalize just
    # as Number.isInteger does in the browser. All accepted bounds are <2^53.
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def normalize_recipe(value):
    """Return a normalized independent recipe, rejecting all nonrecipe fields."""
    if type(value) is not dict or set(value) != {'format', 'name', 'plan'}:
        raise RecipeError('recipe must contain exactly format, name and plan')
    if value['format'] != FORMAT:
        raise RecipeError('unsupported workflow recipe format')
    name = value['name']
    if (not isinstance(name, str) or not 1 <= len(name) <= 80 or name.strip() != name
            or not name.isprintable()):
        raise RecipeError('recipe name must be 1 to 80 trimmed printable characters')
    if type(value['plan']) is not dict:
        raise RecipeError('recipe plan must be an object')
    plan = dict(value['plan'])
    if isinstance(plan.get('exposures_us'), list):
        plan['exposures_us'] = [_json_integer(item) for item in plan['exposures_us']]
    if 'interval_ms' in plan:
        plan['interval_ms'] = _json_integer(plan['interval_ms'])
    try:
        normalized = normalize_plan(plan, strict=True)
    except (WorkflowError, TypeError, ValueError, OverflowError) as error:
        raise RecipeError(str(error)) from error
    result = {'format': FORMAT, 'name': name, 'plan': normalized}
    try:
        encoded = json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise RecipeError('recipe must be finite UTF-8 JSON') from error
    if len(encoded) > MAX_RECIPE_BYTES:
        raise RecipeError('recipe exceeds 64 KiB')
    return result


def loads_recipe(value):
    """Parse at most 64 KiB of strict UTF-8 JSON, then normalize its recipe."""
    try:
        if isinstance(value, str):
            raw = value.encode('utf-8')
        elif isinstance(value, bytes):
            raw = value
        else:
            raise RecipeError('recipe input must be text or bytes')
        if len(raw) > MAX_RECIPE_BYTES:
            raise RecipeError('recipe exceeds 64 KiB')
        parsed = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
    except RecipeError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise RecipeError('recipe must be valid UTF-8 JSON') from error
    return normalize_recipe(parsed)


def load_recipe(path):
    """Read one bounded regular recipe file without creating anything."""
    try:
        path = Path(path)
        if not stat.S_ISREG(path.stat().st_mode):
            raise RecipeError('recipe input must be a regular file')
        with path.open('rb') as stream:
            raw = stream.read(MAX_RECIPE_BYTES + 1)
    except RecipeError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise RecipeError('recipe file could not be read') from error
    return loads_recipe(raw)


def dumps_recipe(value):
    """Return portable JSON only; never export journal/control/result metadata."""
    normalized = normalize_recipe(value)
    return json.dumps(normalized, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + '\n'


def save_recipe(value, destination):
    """Atomically create a new recipe file; never overwrite an existing entry."""
    content = dumps_recipe(value).encode('utf-8')
    temporary = None
    try:
        target = Path(os.path.abspath(os.fspath(destination)))
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='wb', dir=target.parent, prefix='.hf-recipe-', suffix='.tmp',
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    except FileExistsError as error:
        raise RecipeError('recipe destination already exists') from error
    except (OSError, ValueError, TypeError) as error:
        raise RecipeError('recipe file could not be saved') from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
