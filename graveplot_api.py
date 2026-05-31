"""Python facade for GRAVEPlot scripting.

This module is injected as the prelude for batch scripts and exposes:
- Plot builders (for example, `build_line_plot`)
- Dataset builders (for example, `build_dataset_from_file`)
- Dataset utilities and transforms/resampling helpers

Expected global:
    api: Java `PlotScriptingApi` host object provided by GRAVEPlot.
"""

import json
import re
import os, sys
from bisect import bisect_left
import math

# ---------------- utils ----------------

def _snake_to_camel(name: str) -> str:
    if not name or any(c.isupper() for c in name): 
        return name
    parts = name.split("_")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:] if p)


def _camel_to_snake(name: str) -> str:
    if not name:
        return name
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _path_to_python(path: str) -> str:
    """
    Convert a Java-style metadata path like 'axis.tickLabelGap'
    to a Pythonic path like 'axis.tick_label_gap'.
    """
    parts = [p for p in (path or "").split(".") if p]
    return ".".join(_camel_to_snake(p) for p in parts)


def _cap_first(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _safe_has_member(bean_proxy, name: str) -> bool:
    try:
        _ = getattr(bean_proxy, name)
        return True
    except Exception:
        return False


def _resolve_attr_chain(root, chain: list[str]):
    obj = root
    for c in chain:
        if obj is None:
            return None
        try:
            obj = getattr(obj, c)
        except Exception:
            return None
    return obj


def _jsonable(obj):
    """
    Recursively convert Graal/host/foreign objects to JSON-safe
    Python primitives (dict/list/str/number/bool/None).
    """
    # Primitives
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # Mapping-like
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}

    # Sequence-like
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]

    # Fallback: just string-ify
    return str(obj)


def _editor_type_to_py_type(editor_type: str | None) -> str:
    et = (editor_type or "").lower()
    if et == "boolean":
        return "bool"
    if et in ("integer", "int", "slider-int"):
        return "int"
    if et in ("double", "float", "slider", "number"):
        return "float"
    # text, choice, color, etc.
    return "str"


def _serialization_to_py_type(serial_type: str | None) -> str:
    s = (serial_type or "").lower()
    if s in ("boolean", "java.lang.boolean"):
        return "bool"
    if s in ("integer", "int", "java.lang.integer"):
        return "int"
    if s in ("double", "float", "java.lang.double", "java.lang.float"):
        return "float"
    return "str"

def _is_finite_number(v) -> bool:
    try:
        return v is not None and math.isfinite(float(v))
    except Exception:
        return False


def _prepare_xy(xs, ys):
    """
    - Filters non-numeric points
    - Sorts by X
    - De-duplicates equal X by averaging Y
    Returns (xs_sorted_unique, ys_sorted_unique)
    """
    pts = []
    for x, y in zip(list(xs or []), list(ys or [])):
        if _is_finite_number(x) and _is_finite_number(y):
            pts.append((float(x), float(y)))

    if not pts:
        return [], []

    pts.sort(key=lambda p: p[0])

    # Deduplicate Xs by averaging Ys for identical X.
    out_x = []
    out_y = []
    i = 0
    n = len(pts)
    while i < n:
        x0 = pts[i][0]
        sumy = 0.0
        cnt = 0
        j = i
        while j < n and pts[j][0] == x0:
            sumy += pts[j][1]
            cnt += 1
            j += 1
        out_x.append(x0)
        out_y.append(sumy / max(cnt, 1))
        i = j

    return out_x, out_y


def _linear_interp_extrap(xs_src, ys_src, xs_new, extrapolate=True):
    """
    Piecewise-linear interpolation (and optional extrapolation) of ys_src(xs_src) at xs_new.
    xs_src must be sorted, unique, length >= 2 for interpolation.
    """
    xs_src, ys_src = _prepare_xy(xs_src, ys_src)

    m = len(xs_src)
    if m == 0:
        return [None for _ in (xs_new or [])]
    if m == 1:
        # Only one point: constant if extrapolate else None outside exact match
        x0, y0 = xs_src[0], ys_src[0]
        out = []
        for x in xs_new or []:
            if not _is_finite_number(x):
                out.append(None)
                continue
            xf = float(x)
            if xf == x0:
                out.append(y0)
            else:
                out.append(y0 if extrapolate else None)
        return out

    out = []
    for x in (xs_new or []):
        if not _is_finite_number(x):
            out.append(None)
            continue

        x = float(x)

        # Find insertion point in xs_src
        i = bisect_left(xs_src, x)

        if i == 0:
            # Left of first point
            if not extrapolate:
                out.append(None)
                continue
            x0, y0 = xs_src[0], ys_src[0]
            x1, y1 = xs_src[1], ys_src[1]
            # Linear extrapolate using first segment
            t = (x - x0) / (x1 - x0) if (x1 - x0) != 0 else 0.0
            out.append(y0 + t * (y1 - y0))
            continue

        if i >= m:
            # Right of last point
            if not extrapolate:
                out.append(None)
                continue
            x0, y0 = xs_src[m - 2], ys_src[m - 2]
            x1, y1 = xs_src[m - 1], ys_src[m - 1]
            t = (x - x1) / (x1 - x0) if (x1 - x0) != 0 else 0.0
            out.append(y1 + t * (y1 - y0))
            continue

        # Between xs_src[i-1] and xs_src[i]
        x0, y0 = xs_src[i - 1], ys_src[i - 1]
        x1, y1 = xs_src[i], ys_src[i]
        if x1 == x0:
            out.append(y0)  # should not happen after dedupe, but safe
            continue
        t = (x - x0) / (x1 - x0)
        out.append(y0 + t * (y1 - y0))

    return out


def resample_dataset_to_reference_x(
    reference: "Dataset",
    source: "Dataset",
    *,
    name: str | None = None,
    extrapolate: bool = True,
    keep_reference_units: bool = True,
):
    """
    Returns a NEW Dataset whose X values are EXACTLY reference.x, and whose Y values
    come from interpolating/extrapolating the source dataset onto those Xs.

    - keep_reference_units=True:
        X units from reference, Y units from source.
      False:
        X units from source, Y units from source (rarely what you want).

    Notes:
    - Does not mutate either input dataset.
    - Uses piecewise-linear interpolation.
    - Filters non-finite points and averages duplicate Xs in the source.
    """
    if not isinstance(reference, Dataset) or not isinstance(source, Dataset):
        raise TypeError("resample_dataset_to_reference_x expects (Dataset, Dataset)")

    ref_x = reference.x
    src_x = source.x
    src_y = source.y

    new_y = _linear_interp_extrap(src_x, src_y, ref_x, extrapolate=extrapolate)

    # Units policy
    try:
        ref_xu = getattr(reference._j, "getIndependentUnits", lambda: "")() or ""
        ref_yu = getattr(reference._j, "getDependentUnits",  lambda: "")() or ""
    except Exception:
        ref_xu = ref_yu = ""

    try:
        src_xu = getattr(source._j, "getIndependentUnits", lambda: "")() or ""
        src_yu = getattr(source._j, "getDependentUnits",  lambda: "")() or ""
    except Exception:
        src_xu = src_yu = ""

    x_units = ref_xu if keep_reference_units else src_xu
    y_units = src_yu

    out_name = name
    if out_name is None:
        # default naming that reads well in UI
        out_name = f"{source.name or 'source'} (resampled to {reference.name or 'reference'})"

    # Build the new dataset registered in Java and return it.
    return build_dataset_from_lists(out_name, x_units, y_units, ref_x, new_y)

def _cubic_spline_coeffs(xs, ys):
    """
    Natural cubic spline.
    xs must be sorted and unique (use _prepare_xy first).
    Returns arrays a,b,c,d such that on interval i:
      S_i(x) = a[i] + b[i]*t + c[i]*t^2 + d[i]*t^3,  t = x - xs[i]
    """
    n = len(xs)
    if n < 2:
        return ys[:], [], [0.0] * n, []

    a = ys[:]
    b = [0.0] * (n - 1)
    c = [0.0] * n
    d = [0.0] * (n - 1)

    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    # Guard (shouldn’t happen if xs unique, but keep it safe)
    for i in range(len(h)):
        if h[i] == 0.0:
            h[i] = 1e-12

    alpha = [0.0] * n
    for i in range(1, n - 1):
        alpha[i] = (3.0 / h[i]) * (a[i + 1] - a[i]) - (3.0 / h[i - 1]) * (a[i] - a[i - 1])

    l = [1.0] * n
    mu = [0.0] * n
    z = [0.0] * n

    for i in range(1, n - 1):
        l[i] = 2.0 * (xs[i + 1] - xs[i - 1]) - h[i - 1] * mu[i - 1]
        if l[i] == 0.0:
            l[i] = 1e-12
        mu[i] = h[i] / l[i]
        z[i] = (alpha[i] - h[i - 1] * z[i - 1]) / l[i]

    # Natural spline boundary conditions: c[0] = c[n-1] = 0 already
    for j in reversed(range(n - 1)):
        c[j] = z[j] - mu[j] * c[j + 1]
        b[j] = ((a[j + 1] - a[j]) / h[j]) - (h[j] * (c[j + 1] + 2.0 * c[j]) / 3.0)
        d[j] = (c[j + 1] - c[j]) / (3.0 * h[j])

    return a, b, c, d


def _eval_cubic(xs, coeffs, x):
    """
    Evaluate a natural cubic spline at x.
    xs must be sorted, unique.
    coeffs is (a,b,c,d) from _cubic_spline_coeffs.
    """
    a, b, c, d = coeffs
    n = len(xs)
    if n == 0:
        return None
    if n == 1:
        return a[0]

    # choose interval i so xs[i] <= x <= xs[i+1]
    i = bisect_left(xs, x) - 1
    if i < 0:
        i = 0
    if i > n - 2:
        i = n - 2

    t = x - xs[i]
    return a[i] + b[i] * t + c[i] * (t ** 2) + d[i] * (t ** 3)


def resample_linear(reference, source, *, name=None, extrapolate=True):
    """Resample ``source`` onto ``reference.x`` using linear interpolation.

    Args:
        reference: Target ``Dataset`` that supplies X values.
        source: Source ``Dataset`` that supplies Y values.
        name: Optional name for the returned dataset.
        extrapolate: If True, use end-segment linear extrapolation outside range.

    Returns:
        Dataset: Newly built dataset registered in the Java model.
    """
    return resample_dataset_to_reference_x(
        reference,
        source,
        name=name,
        extrapolate=extrapolate
    )

def _linear_extrapolate_ends(xs, ys, x):
    """
    Fast linear extrapolation using the first or last segment.
    xs must be sorted, unique, len(xs) >= 2.
    """
    if x <= xs[0]:
        x0, y0 = xs[0], ys[0]
        x1, y1 = xs[1], ys[1]
        t = (x - x0) / (x1 - x0) if (x1 - x0) != 0 else 0.0
        return y0 + t * (y1 - y0)
    else:
        x0, y0 = xs[-2], ys[-2]
        x1, y1 = xs[-1], ys[-1]
        t = (x - x1) / (x1 - x0) if (x1 - x0) != 0 else 0.0
        return y1 + t * (y1 - y0)


def resample_cubic(reference, source, *, name=None, extrapolate=True, keep_reference_units=True):
    """Resample ``source`` onto ``reference.x`` using a natural cubic spline.

    Falls back to linear resampling when there are not enough source points
    to build a stable spline.
    """
    if not isinstance(reference, Dataset) or not isinstance(source, Dataset):
        raise TypeError("resample_cubic expects (Dataset, Dataset)")

    ref_x = reference.x
    xs, ys = _prepare_xy(source.x, source.y)

    # Not enough points for a stable spline: fall back
    if len(xs) < 3:
        return resample_linear(reference, source, name=name, extrapolate=extrapolate)

    coeffs = _cubic_spline_coeffs(xs, ys)

    new_y = []
    x_min = xs[0]
    x_max = xs[-1]

    for x in ref_x:
        if not _is_finite_number(x):
            new_y.append(None)
            continue

        xf = float(x)

        # Out of source range
        if xf < x_min or xf > x_max:
            if extrapolate:
                new_y.append(_linear_extrapolate_ends(xs, ys, xf))
            else:
                new_y.append(None)
            continue

        new_y.append(_eval_cubic(xs, coeffs, xf))

    # Units policy (match linear)
    try:
        ref_xu = getattr(reference._j, "getIndependentUnits", lambda: "")() or ""
    except Exception:
        ref_xu = ""

    try:
        src_xu = getattr(source._j, "getIndependentUnits", lambda: "")() or ""
        src_yu = getattr(source._j, "getDependentUnits",  lambda: "")() or ""
    except Exception:
        src_xu = src_yu = ""

    x_units = ref_xu if keep_reference_units else src_xu
    y_units = src_yu

    out_name = name
    if out_name is None:
        out_name = f"{source.name or 'source'} (cubic resampled to {reference.name or 'reference'})"

    return build_dataset_from_lists(out_name, x_units, y_units, ref_x, new_y)

# ---------------- dataset wrapper (the ONLY way to mutate X/Y) ----------------

class Dataset:
    """
    Thin Python wrapper around a Java ChannelData.
    All X/Y edits (and transforms) go through this object.
    """
    __slots__ = ("_j", "_j_pyplot")

    def __init__(self, j_channel_data, j_pyplot=None):
        self._j = j_channel_data
        self._j_pyplot = j_pyplot  # reserved for future hooks

    # ---- identity/meta ----
    @property
    def ident(self) -> int:
        try:
            return int(self._j.getIdent())
        except Exception:
            return 0

    @property
    def name(self) -> str:
        try:
            return self._j.getChannelName()
        except Exception:
            return ""

    @name.setter
    def name(self, value: str):
        self._j.setChannelName(str(value or ""))

    # ---- units ----
    def set_units(self, x_units: str | None = None, y_units: str | None = None):
        if x_units is not None:
            self._j.setIndependentUnits(str(x_units))
        if y_units is not None:
            self._j.setDependentUnits(str(y_units))
        return self

    # ---- X/Y accessors ----
    @property
    def x(self):
        return list(self._j.getIndependent() or [])

    @x.setter
    def x(self, xs):
        self._j.setIndependent(list(xs or []))

    @property
    def y(self):
        return list(self._j.getDependent() or [])

    @y.setter
    def y(self, ys):
        self._j.setDependent(list(ys or []))

    def set_xy(self, xs, ys):
        xs = list(xs or [])
        ys = list(ys or [])
        if len(xs) != len(ys):
            raise ValueError("xs and ys must be the same length")
        self._j.setIndependent(xs)
        self._j.setDependent(ys)
        return self

    def append(self, xs, ys):
        xs = list(xs or [])
        ys = list(ys or [])
        if len(xs) != len(ys):
            raise ValueError("xs and ys must be the same length")
        curx = list(self._j.getIndependent() or [])
        cury = list(self._j.getDependent() or [])
        curx.extend(xs)
        cury.extend(ys)
        self._j.setIndependent(curx)
        self._j.setDependent(cury)
        return self

    def clear(self):
        self._j.setIndependent([])
        self._j.setDependent([])
        return self

    def pairs(self):
        xs = self._j.getIndependent() or []
        ys = self._j.getDependent() or []
        return list(zip(xs, ys))

    # ---- transformation integration ----
    def _unwrap_associated(self, datasets):
        """Normalize `datasets` to a List[ChannelData] or None."""
        if datasets is None:
            return None
        # single Dataset
        if isinstance(datasets, Dataset):
            return [datasets._j]
        # iterable of Datasets
        if isinstance(datasets, (list, tuple)):
            out = []
            for d in datasets:
                if not isinstance(d, Dataset):
                    raise TypeError("Associated datasets must be Dataset instances.")
                out.append(d._j)
            return out
        raise TypeError("datasets must be a Dataset, a list/tuple of Datasets, or None")

    def transform(self, transform_type: str, datasets=None, **params):
        """
        Apply a backend transform to this dataset IN-PLACE.
        `datasets` may be:
          - None
          - a single Dataset
          - a list/tuple of Datasets

        Delegates to api.transformDataset(primary_cd, associated_cds, transform_type, params).
        If the service returns a new ChannelData, adopt it.
        """
        fn = getattr(api, "transformDataset", None)
        if not callable(fn):
            raise RuntimeError("transformDataset API not available on 'api'")
        associated = self._unwrap_associated(datasets)
        result = fn(self._j, associated, transform_type, params)
        if result is not None:
            self._j = result
        return self

    def preview_transform(self, transform_type: str, datasets=None, **params):
        """
        Non-destructive preview via clone + transform.
        Returns {'x': [...], 'y': [...]} for the transformed data.
        """
        clone = self.clone()
        clone.transform(transform_type, datasets=datasets, **params)
        return {"x": clone.x, "y": clone.y}

    def clone(self):
        """
        Shallow clone of underlying ChannelData without a cloneChannelData API.
        Rebuilds via build_dataset_from_lists and preserves units.
        """
        name = self.name or ""
        try:
            xu = getattr(self._j, "getIndependentUnits", lambda: "")() or ""
            yu = getattr(self._j, "getDependentUnits",  lambda: "")() or ""
        except Exception:
            xu = yu = ""
        built_ds = build_dataset_from_lists(name, xu, yu, self.x, self.y)  # returns Dataset
        return Dataset(built_ds._j, self._j_pyplot)

    # ---- advanced interop (optional) ----
    def to_java(self):
        """Return the underlying ChannelData (advanced/interop use)."""
        return self._j

    def resample_linear_like(self, reference, *, name=None, extrapolate=True, keep_reference_units=True):
        return resample_dataset_to_reference_x(
            reference,
            self,
            name=name,
            extrapolate=extrapolate,
            keep_reference_units=keep_reference_units
        )

    def resample_cubic_like(self, reference, *, name=None, extrapolate=True, keep_reference_units=True):
        return resample_cubic(
            reference,
            self,
            name=name,
            extrapolate=extrapolate,
            keep_reference_units=keep_reference_units
        )

# ---------------- generic bean list plumbing ----------------

class BeanItem:
    """
    One logical bean at a base path, e.g. 'axis.x' or 'channels.0'.
    - Writes go through j_pyplot.setPropertyPath(f"{base}.{name}", value).
    - Reads try j_props traversal first; if that fails, tries Java-style getters
      on a provided concrete bean (via setter_bean_supplier).
    """
    __slots__ = ("j_pyplot", "j_props", "base", "_setter_bean_supplier")

    def __init__(self, j_pyplot, j_props, base: str, setter_bean_supplier=None):
        object.__setattr__(self, "j_pyplot", j_pyplot)
        object.__setattr__(self, "j_props",  j_props)
        object.__setattr__(self, "base",     base)
        object.__setattr__(self, "_setter_bean_supplier", setter_bean_supplier)

    def __setattr__(self, name, value):
        supplier = object.__getattribute__(self, "_setter_bean_supplier")
        if supplier is not None:
            try:
                bean = supplier()
                if bean is not None:
                    cap = _cap_first(_snake_to_camel(name))
                    setter = getattr(bean, f"set{cap}", None)
                    if callable(setter):
                        setter(value)
                        return
            except Exception:
                pass
        base = object.__getattribute__(self, "base")
        jp   = object.__getattribute__(self, "j_pyplot")
        jp.setPropertyPath(f"{base}.{_snake_to_camel(name)}", value)

    def __getattr__(self, name):
        j_props = object.__getattribute__(self, "j_props")
        base    = object.__getattribute__(self, "base")
        chain   = [p for p in base.split(".") if p] + [_snake_to_camel(name)]
        val = _resolve_attr_chain(j_props, chain)
        if val is not None:
            return val
        supplier = object.__getattribute__(self, "_setter_bean_supplier")
        if supplier is not None:
            try:
                bean = supplier()
                if bean is not None:
                    cap = _cap_first(_snake_to_camel(name))
                    for m in (f"get{cap}", f"is{cap}", _snake_to_camel(name)):
                        fn = getattr(bean, m, None)
                        if callable(fn):
                            return fn()
            except Exception:
                pass
        return None


class KeyedBeanList:
    """
    A list/dict hybrid of named BeanItems (e.g., {'x': 'axis.x', 'y':'axis.y'}).
    - Index by key: plot.axis['x']
    - Dot access via __getattr__: plot.axis.x
    - Iterates in key order.
    """
    def __init__(self, j_pyplot, j_props, key_to_base: dict[str, str], setters: dict[str, callable] | None = None):
        self._j_pyplot = j_pyplot
        self._j_props  = j_props
        self._keys     = list(key_to_base.keys())
        self._bases    = key_to_base
        self._setters  = setters or {}

    def __len__(self):
        return len(self._keys)

    def __iter__(self):
        for k in self._keys:
            yield self[k]

    def __getitem__(self, key):
        if key not in self._bases:
            raise KeyError(key)
        base = self._bases[key]
        supplier = self._setters.get(key)
        return BeanItem(self._j_pyplot, self._j_props, base, supplier)

    def __getattr__(self, name):
        if name in self._bases:
            return self[name]
        raise AttributeError(name)


class IndexBeanList:
    """
    A generic index-based list of BeanItems.
    Configure with:
      - length_fn: () -> int
      - base_resolver: (index:int) -> str (e.g., f"channels.{index}")
      - setter_supplier: optional (index:int) -> concrete bean (for Java setters)
    """
    def __init__(self, j_pyplot, j_props, length_fn, base_resolver, setter_supplier=None):
        self._j_pyplot = j_pyplot
        self._j_props  = j_props
        self._len_fn   = length_fn
        self._base_resolver   = base_resolver
        self._setter_supplier = setter_supplier

    def __len__(self):
        try:
            return int(self._len_fn() or 0)
        except Exception:
            return 0

    def __getitem__(self, idx):
        n = len(self)
        if idx < 0:
            idx = n + idx
        if idx < 0 or idx >= n:
            raise IndexError("index out of range")
        base = self._base_resolver(idx)
        supplier = (lambda i=idx: self._setter_supplier(i)) if self._setter_supplier else None
        return BeanItem(self._j_pyplot, self._j_props, base, supplier)


# ---------------- sections ----------------

class _NullSection:
    def __init__(self, *_a, **_kw): pass
    def __setattr__(self, _n, _v): pass
    def __getattr__(self, _n): return None


class _BeanSection:
    """Writes via setPropertyPath; reads through props() if available."""
    __slots__ = ("j_pyplot", "j_props", "_base")
    def __init__(self, j_pyplot, j_props, base: str):
        object.__setattr__(self, "j_pyplot", j_pyplot)
        object.__setattr__(self, "j_props", j_props)
        object.__setattr__(self, "_base", base)

    def __setattr__(self, name, value):
        if name in _BeanSection.__slots__:
            object.__setattr__(self, name, value); return
        self.j_pyplot.setPropertyPath(f"{self._base}.{_snake_to_camel(name)}", value)

    def __getattr__(self, name):
        try:
            obj = getattr(self.j_props, self._base, None)
            if obj is None:
                return None
            return getattr(obj, _snake_to_camel(name), None)
        except Exception:
            return None


# ---------------- base: GenericPlot ----------------

class GenericPlot:
    """
    Base plot facade (no XY assumptions).
      - title, legend, chart sections (if present)
      - add_dataset(s) return Dataset handles (mutations ONLY via Dataset)
      - render_html / render helpers
      - property introspection via describe_properties[_text]
    """
    def __init__(self, plot_type_name: str, name: str | None):
        self.j_pyplot = api.newPlot(plot_type_name)
        self.j_props  = self.j_pyplot.props()

        self.title   = _BeanSection(self.j_pyplot, self.j_props, "title")  if _safe_has_member(self.j_props, "title")  else _NullSection()
        self.legend  = _BeanSection(self.j_pyplot, self.j_props, "legend") if _safe_has_member(self.j_props, "legend") else _NullSection()
        self.margins = _BeanSection(self.j_pyplot, self.j_props, "chart")  if _safe_has_member(self.j_props, "chart")  else _NullSection()

        if name is not None:
            self.title.title_text = name

    # ---- batch attach: returns list[Dataset] ----
    def add_datasets(self, *args):
        """
        Accepts either multiple Dataset arguments or a single iterable of Datasets.
        Returns a list of Dataset handles (as attached to this plot).
        """
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            datasets = args[0]
        else:
            datasets = args

        items = []
        for ds in datasets:
            if not isinstance(ds, Dataset):
                raise TypeError("add_datasets expects Dataset objects. Use build_dataset_* to create them.")
            items.append(self.add_dataset(ds))
        return items

    # ---- single attach: returns Dataset (the ONLY mutator surface) ----
    def add_dataset(self, dataset: Dataset):
        """
        Register a Dataset with the plot and return a (possibly re-wrapped) Dataset.
        Strict Dataset-only API: pass a Dataset, not a ChannelData.
        """
        if not isinstance(dataset, Dataset):
            raise TypeError("add_dataset expects a Dataset. Use build_dataset_* to create one.")
        ch = self.j_pyplot.addDataset(dataset._j)
        ch = ch or dataset._j
        return Dataset(ch, self.j_pyplot)

    # ---- rendering ----
    def render_html(self):
        return self.j_pyplot.renderHtml()

    def render(self, out_path, width=1200, height=800):
        self.j_pyplot.render(out_path, width, height)

    # ---- property catalog / introspection ----
    def describe_properties(self):
        """
        Return the raw metadata dict from the backend, normalized to
        JSON-safe Python objects (dict/list/str/number/bool/None).

        {
          "plotType": "...",
          "properties": [ { path, name, optional, editorType, help, category,
                            serializationClassType, defaultValue }, ... ],
          "tree": { ... }
        }
        """
        try:
            raw = self.j_pyplot.describeProperties()
            # Convert any foreign Java/Graal objects to plain Python structures
            return _jsonable(raw)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def _prop_python_type(self, p: dict) -> str:
        """
        Decide a friendly Python type from serializationClassType or editorType.
        """
        ser = p.get("serializationClassType")
        if ser:
            ser = str(ser)
            simple = ser.split(".")[-1]
            mapping = {
                "String": "str",
                "Integer": "int",
                "Long": "int",
                "Double": "float",
                "Float": "float",
                "Boolean": "bool",
            }
            return mapping.get(simple, simple)

        et = (p.get("editorType") or "").lower()
        if et in ("text", "choice", "color"):
            return "str"
        if et in ("integer", "int"):
            return "int"
        if et in ("double", "number", "float"):
            return "float"
        if et in ("boolean", "bool"):
            return "bool"
        return "any"

    def describe_properties_text(self, include_help: bool = True) -> str:
        """
        Human-readable summary, grouped by top-level section.
        """
        info = self.describe_properties()

        # If we got an error dict, surface it clearly
        if isinstance(info, dict) and "error" in info and not info.get("properties"):
            return f"[describe_properties error] {info['error']}"

        if not isinstance(info, dict):
            return str(info)

        props = info.get("properties") or []
        tree = info.get("tree") or {}

        # Group by top-level segment
        sections: dict[str, dict] = {}

        for p in props:
            path = p.get("path")
            if not path:
                continue
            parts = [seg for seg in path.split(".") if seg]
            if not parts:
                continue
            root = parts[0]
            
            sec = sections.setdefault(root, {"meta": None, "items": []})
            if sec["meta"] is None:
                node = tree.get(root) or {}
                sec["meta"] = node.get("__meta") or {}

            py_path = _path_to_python(path)
            py_type = self._prop_python_type(p)
            help_text = (p.get("help") or "").strip()

            sec["items"].append(
                {
                    "path": path,
                    "py_path": py_path,
                    "py_type": py_type,
                    "help": help_text,
                }
            )

        lines: list[str] = []

        # Stable order by section name
        for root in sorted(sections.keys()):
            sec = sections[root]
            meta = sec["meta"] or {}
            display_name = meta.get("name") or root
            header = f"{display_name} ({root})"
            lines.append(header)

            items = sorted(sec["items"], key=lambda it: it["py_path"])

            for it in items:
                py_path = it["py_path"]
                py_type = it["py_type"]
                help_text = it["help"]

                if include_help and help_text:
                    # Inline comment style, nice for copy/paste into scripts
                    lines.append(f"  {py_path}, {py_type}  # {help_text}")
                else:
                    lines.append(f"  {py_path}, {py_type}")

            lines.append("")

        return "\n".join(lines).rstrip()

    def print_properties(self, include_help: bool = True):
        """
        Convenience: print the sectioned property summary directly.
        """
        print(self.describe_properties_text(include_help=include_help))

    # ---- transformation catalog passthroughs ----
    def list_transform_types(self):
        """
        Returns a list[str] of available transform names from the backend.
        """
        try:
            return list(api.listTransformTypes() or [])
        except Exception:
            return []

    def get_transform_properties(self):
        """
        Returns a dict[str, list[TransformProperty]] describing parameters
        for each available transform (as provided by the backend).
        """
        try:
            return dict(api.getTransformProperties() or {})
        except Exception:
            return {}

    # friendly aliases
    add_data_set  = add_dataset
    add_data_sets = add_datasets


# ---------------- XY specialization: GenericXYAxisPlot ----------------

class GenericXYAxisPlot(GenericPlot):
    """
    XY plot facade.

    - Datasets are added via add_dataset(s); Java wires channels -> datasets.
    - Axis styling via plot.axis.x / plot.axis.y
    - Series styling via plot.series[index].<property> (channels.{index})
      (Series wiring is handled automatically on the Java side; Python only
       styles by index.)
    """
    def __init__(self, plot_type_name: str, name: str | None):
        super().__init__(plot_type_name, name)

        # Helper to get the actual List<Axis> from the Java props
        def _axis_list():
            try:
                return getattr(self.j_props, "axis", None)
            except Exception:
                return None

        def _axis_supplier(i):
            lst = _axis_list()
            try:
                return lst[i] if lst is not None else None
            except Exception:
                return None

        # Named aliases: plot.axis.x, plot.axis.y
        self.axis = KeyedBeanList(
            self.j_pyplot,
            self.j_props,
            key_to_base={"x": "axis.0", "y": "axis.1"},
            setters={"x": lambda: _axis_supplier(0), "y": lambda: _axis_supplier(1)}
        )

        # Series list over channels.{i} – styling only.
        # Wiring (channels[i].referencedChannelIdent -> datasets[i].ident)
        # is performed in Java (PlotScriptingApi.rewireSeriesToDatasets).
        self.series = IndexBeanList(
            self.j_pyplot,
            self.j_props,
            length_fn=lambda: self.j_pyplot.getSeriesCount(),
            base_resolver=lambda i: f"channels.{i}",
            setter_supplier=lambda i: self.j_pyplot.getPlotDataAt(i)
        )

    def viewport(self, x=None, y=None):
        """
        Set axis ranges using Axis.data_min / Axis.data_max.

        Args:
            x: (min, max) or {"min": ..., "max": ...} for the X axis.
               Use None for either bound to clear that bound.
            y: (min, max) or {"min": ..., "max": ...} for the Y axis.
               Use None for either bound to clear that bound.

        Returns:
            self
        """
        def _parse_range(r):
            if r is None:
                return None, None, False  # nothing to do
            if isinstance(r, dict):
                return r.get("min"), r.get("max"), True
            if isinstance(r, (list, tuple)) and len(r) == 2:
                return r[0], r[1], True
            raise ValueError("Range must be a 2-tuple/list or a dict with 'min'/'max'.")

        def _coerce(v):
            if v is None:
                return None
            try:
                return float(v)
            except Exception:
                raise ValueError(f"Range bound {v!r} is not numeric or None")

        xmin, xmax, do_x = _parse_range(x)
        ymin, ymax, do_y = _parse_range(y)

        if do_x:
            ax = self.axis.x
            ax.data_min = _coerce(xmin)
            ax.data_max = _coerce(xmax)

        if do_y:
            ay = self.axis.y
            ay.data_min = _coerce(ymin)
            ay.data_max = _coerce(ymax)

        return self

    def clear_viewport(self):
        """
        Clears any fixed axis limits (data_min / data_max) on both X and Y axes.
        This reverts them to automatic scaling in ECharts.
        """
        try:
            self.axis.x.data_min = None
            self.axis.x.data_max = None
        except Exception:
            pass

        try:
            self.axis.y.data_min = None
            self.axis.y.data_max = None
        except Exception:
            pass

        return self


# ---------- friendly builders ----------

def build_line_plot(name: str | None = None) -> GenericXYAxisPlot:
    """Create a simple line plot facade."""
    return GenericXYAxisPlot("Simple Line Plot", name)

def build_scatter_plot(name: str | None = None) -> GenericXYAxisPlot:
    """Create a simple scatter plot facade."""
    return GenericXYAxisPlot("Simple Scatter Plot", name)

def build_comparison_plot(name: str | None = None) -> GenericXYAxisPlot:
    """Create a comparison plot facade."""
    return GenericXYAxisPlot("Comparison Plot", name)

def build_area_plot(name: str | None = None) -> GenericXYAxisPlot:
    """Create a basic area plot facade."""
    return GenericXYAxisPlot("Basic Area Plot", name)

def build_smooth_line_plot(name: str | None = None) -> GenericXYAxisPlot:
    """Create a smoothed line plot facade."""
    return GenericXYAxisPlot("Smoothed Line Plot", name)

def build_pie_chart_plot(name: str | None = None) -> GenericPlot:
    """Create a pie chart plot facade."""
    return GenericPlot("Pie Chart", name)

# ---------- Dataset builders (construct & REGISTER via Java API) ----------

def build_dataset_from_pairs(name, x_label, y_label, pairs):
    """
    pairs: iterable of (x, y)
    Registers in the Java model and returns a Dataset (with ident assigned).
    """
    cd = api.buildDataset(name, x_label, y_label, pairs)
    return Dataset(cd)


def build_dataset_from_lists(name, x_label, y_label, xs, ys):
    """Build and register a dataset from separate X and Y iterables.

    Prefers the backend `buildDatasetFromLists` API when available and falls
    back to zipping pairs.
    """
    try:
        build_lists = getattr(api, "buildDatasetFromLists", None)
        if build_lists is not None:
            cd = build_lists(xs, ys, name, x_label, y_label)
            return Dataset(cd)
    except Exception:
        pass
    pairs = list(zip(xs or [], ys or []))
    cd = api.buildDataset(name, x_label, y_label, pairs)
    return Dataset(cd)


def build_dataset_from_file(file_path, code_type, code_file_type, use_si, channel_name):
    """Build and register a dataset by importing a channel from a plot file."""
    cd = api.buildDatasetFromFile(file_path, code_type, code_file_type, use_si, channel_name)
    return Dataset(cd)


def build_dataset_from_spreadsheet(
    file_path,
    start_row,
    end_row,
    independent_column,
    dependent_column,
    independent_label,
    dependent_label,
    channel_name=None,
):
    """
    Build and register a dataset by reading numeric values from a spreadsheet-like file.

    Args:
        file_path: Path to CSV/TSV/TXT spreadsheet export.
        start_row: 1-based start row (inclusive).
        end_row: 1-based end row (inclusive), or None/'' for end-of-file.
        independent_column: Independent variable column (e.g., 'A' or '1').
        dependent_column: Dependent variable column (e.g., 'B' or '2').
        independent_label: Label/unit for X values.
        dependent_label: Label/unit for Y values.
        channel_name: Optional dataset/channel name. Defaults to dependent_label when blank.
    """
    cd = api.buildDatasetFromSpreadsheet(
        file_path,
        int(start_row),
        end_row,
        independent_column,
        dependent_column,
        independent_label,
        dependent_label,
        channel_name,
    )
    return Dataset(cd)


# ---------- Transform catalog (module-level convenience) ----------

def list_transform_types():
    """
    Returns a list[str] of available transform names from the backend.
    """
    try:
        return list(api.listTransformTypes() or [])
    except Exception:
        return []


def get_transform_properties():
    """
    Returns a dict[str, list[TransformProperty]] describing parameters
    for each available transform (as provided by the backend).
    """
    try:
        return dict(api.getTransformProperties() or {})
    except Exception:
        return {}
