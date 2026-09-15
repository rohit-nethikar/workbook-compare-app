"""Workbook Compare engine: parse .twb XML, normalize structure, compute diffs."""
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorkbookStructure:
    """Normalized workbook structure extracted from .twb XML."""
    worksheets: set = field(default_factory=set)
    dashboards: set = field(default_factory=set)
    fields: dict = field(default_factory=dict)  # {internal_name: {caption, datatype, role, ds, formula, aggregation, format, comment}}
    parameters: dict = field(default_factory=dict)  # {internal_name: {caption, datatype, value}}
    filters: dict = field(default_factory=dict)  # {worksheet_name: {field_internal_name: filter_detail}}
    datasource_filters: dict = field(default_factory=dict)  # {ds_caption: {field_internal_name: filter_detail}}
    visuals: dict = field(default_factory=dict)  # {worksheet_name: {marks, encodings, sorts}}
    dash_zones: dict = field(default_factory=dict)  # {dashboard_name: {zone_key: {type, name}}}
    connections: dict = field(default_factory=dict)  # {ds_caption: [{class, server, port, ...}]}
    groups: dict = field(default_factory=dict)  # {internal_name: {caption, members, ds}} - Groups and value-list Sets share the same <groupfilter>-under-<column> XML shape and aren't reliably distinguishable from that element alone, so both land in this one generic bucket.
    computed_sets: dict = field(default_factory=dict)  # {internal_name: {caption, formula, ds}} - best-effort: keyed off a <calculation class="tableau-app:set-computed"> pattern not independently verified against a real Tableau export; degrades to "not detected" if the class string differs.
    actions: dict = field(default_factory=dict)  # {action_name: {type}} - top-level <actions>/<action> (filter/highlight/url/parameter)
    hidden_sheets: set = field(default_factory=set)  # worksheet names with no visible <windows>/<window class="worksheet"> tab
    sheet_windows_known: bool = False  # whether this workbook's XML carries a <windows> section at all - hidden_sheets is only meaningful when this is True on both sides of a diff
    filter_cards: dict = field(default_factory=dict)  # {worksheet_name: {field_internal_name: True}} - <slices> quick-filter-card visibility, distinct from the <filter> condition itself


def _normalize_column_ref(ref: str) -> str:
    """Normalize a column reference by stripping volatile federated hash.

    Example: '[federated.abc123].[Sales]' -> '[Sales]'
    """
    if not ref:
        return ref
    # Match pattern like [federated.hash] or keep simple [FieldName]
    match = re.match(r'^\[federated\.[^\]]*\]\.?(.*)$', ref)
    if match:
        return match.group(1)
    return ref


_FIELD_REF_RE = re.compile(r'\[[^\[\]]+\]')


def _referenced_fields(formula: Optional[str]) -> set:
    """Field tokens referenced in a calc formula. Formula-internal [Foo]
    tokens are always plain field refs (Tableau calc syntax uses quotes for
    string literals, never brackets), so no federated-hash normalization is
    needed here unlike column= refs elsewhere in this module."""
    if not formula:
        return set()
    return {m.group(0) for m in _FIELD_REF_RE.finditer(formula)}


def _xml_bool(v) -> Optional[bool]:
    """Tableau XML represents booleans as the strings 'true'/'false'."""
    if v is None:
        return None
    return str(v).strip().lower() == 'true'


def _parse_filter_detail(filt) -> dict:
    """Best-effort extraction of a <filter> element's condition detail.

    Schema names here are not verified against a real Tableau export (see
    workbook_compare notes) - every lookup degrades to "key absent" rather
    than raising or guessing, so a wrong assumption only under-detects.
    Only includes keys that were actually found.
    """
    out = {}
    try:
        cls = filt.get('class')
        if cls:
            out['class'] = cls

        members = sorted({
            gf.get('member') for gf in filt.findall('.//groupfilter')
            if gf.get('member')
        })
        if members:
            out['members'] = members

        direct_gf = filt.find('./groupfilter')
        if direct_gf is not None and direct_gf.get('function'):
            out['exclude'] = (direct_gf.get('function') in ('except', 'exclude'))

        min_v = filt.get('min')
        if min_v is None:
            el = filt.find('./min')
            min_v = el.text if el is not None else None
        max_v = filt.get('max')
        if max_v is None:
            el = filt.find('./max')
            max_v = el.text if el is not None else None
        if min_v is not None:
            out['min'] = min_v
        if max_v is not None:
            out['max'] = max_v

        ctx = _xml_bool(filt.get('context'))
        if ctx is not None:
            out['context'] = ctx

        for attr, key in (
            ('period-type-v2', 'period_type'), ('first-period', 'first_period'),
            ('last-period', 'last_period'), ('include-future', 'include_future'),
            ('include-null', 'include_null'),
        ):
            v = filt.get(attr)
            if v is not None:
                out[key] = _xml_bool(v) if key in ('include_future', 'include_null') else v
    except Exception:
        pass
    return out


def _parse_filter_map(container) -> dict:
    """{normalized_field_key: filter_detail} for every <filter column=...>
    found under `container`."""
    out = {}
    try:
        for filt in container.findall('.//filter'):
            col_ref = filt.get('column')
            if not col_ref:
                continue
            key = _normalize_column_ref(col_ref)
            out[key] = _parse_filter_detail(filt)
    except Exception:
        pass
    return out


def _parse_field_comment(col) -> Optional[str]:
    """Best-effort field comment/description from <desc><formatted-text><run>."""
    try:
        desc = col.find('./desc')
        if desc is None:
            return None
        text = ''.join(r.text or '' for r in desc.findall('.//run')).strip()
        return text or None
    except Exception:
        return None


def _parse_worksheet_visuals(ws) -> dict:
    """Best-effort mark type / encodings / sort order for a worksheet."""
    out = {}
    try:
        marks = sorted({m.get('class') for m in ws.findall('.//mark') if m.get('class')})
        if marks:
            out['marks'] = marks

        enc_set = set()
        for enc in ws.findall('.//encodings/*'):
            col = enc.get('column')
            if col:
                enc_set.add((enc.tag, _normalize_column_ref(col)))
        if enc_set:
            out['encodings'] = [{'encoding': t, 'field': c} for t, c in sorted(enc_set)]

        sort_set = set()
        for s in ws.findall('.//sort'):
            col = s.get('column')
            if col:
                sort_set.add((_normalize_column_ref(col), s.get('direction') or '', s.get('class') or ''))
        if sort_set:
            out['sorts'] = [{'field': f, 'direction': d, 'class': c} for f, d, c in sorted(sort_set)]
    except Exception:
        pass
    return out


def _parse_filter_cards(ws) -> dict:
    """Best-effort quick-filter-card visibility for a worksheet, via
    <slices>/<column> - a field listed here shows a visible filter card on
    the worksheet, which is distinct from <filter> (the applied condition:
    a card can be hidden while its condition still applies, or vice versa).
    Schema not independently verified - degrades to "not detected" if the
    real shape differs, same philosophy as _parse_filter_detail above."""
    out = {}
    try:
        for col in ws.findall('.//slices/column'):
            ref = (col.text or '').strip()
            if ref:
                out[_normalize_column_ref(ref)] = True
    except Exception:
        pass
    return out


def _parse_dashboard_zones(db) -> dict:
    """Best-effort dashboard zone inventory, keyed by type+name since zone
    `id`s are commonly reassigned on every save (would otherwise produce
    false add+remove pairs for unchanged zones)."""
    zones = {}
    try:
        for z in db.findall('.//zone'):
            name = z.get('name')
            if name:
                type_v2 = z.get('type-v2')
                key = f"{type_v2 or 'unknown'}::{name}"
                zones[key] = {'type': type_v2, 'name': name}
    except Exception:
        pass
    return zones


def _extract_twb_from_upload(upload_stream) -> bytes:
    """Extract .twb XML from .twbx zip, or return raw bytes if already .twb."""
    try:
        # Try to read as ZIP (for .twbx)
        with zipfile.ZipFile(upload_stream, 'r') as zf:
            # Find .twb file in the archive
            twb_files = [f for f in zf.namelist() if f.endswith('.twb')]
            if twb_files:
                return zf.read(twb_files[0])
    except (zipfile.BadZipFile, RuntimeError):
        pass

    # Fall back to raw .twb
    upload_stream.seek(0)
    return upload_stream.read()


def parse_workbook(twb_bytes: bytes) -> WorkbookStructure:
    """Parse .twb XML into normalized WorkbookStructure."""
    root = ET.fromstring(twb_bytes)
    struct = WorkbookStructure()

    # Worksheets and Dashboards
    for ws in root.findall('.//worksheet'):
        name = ws.get('name')
        if name:
            struct.worksheets.add(name)

    for db in root.findall('.//dashboard'):
        name = db.get('name')
        if name:
            struct.dashboards.add(name)
            zones = _parse_dashboard_zones(db)
            if zones:
                struct.dash_zones[name] = zones

    # Parameters datasource
    params_ds = root.find(".//datasource[@name='Parameters']")
    if params_ds is not None:
        for col in params_ds.findall('.//column'):
            internal_name = col.get('name')
            if internal_name:
                caption = col.get('caption', internal_name)
                datatype = col.get('datatype', 'string')
                calc = col.find('./calculation')
                value = None
                if calc is not None:
                    value = calc.get('formula')
                if value is None:
                    value = col.get('value')
                param = {
                    'caption': caption,
                    'datatype': datatype,
                    'value': value
                }

                # Allowable-values domain: a "Range" parameter carries
                # min/max/granularity on a <range> element, a "List"
                # parameter carries explicit <members>/<member value= alias=>
                # entries. Best-effort - only included when actually found.
                range_el = col.find('.//range')
                if range_el is not None:
                    range_info = {
                        k: range_el.get(k) for k in ('min', 'max', 'granularity')
                        if range_el.get(k) is not None
                    }
                    if range_info:
                        param['range'] = range_info

                members_el = col.find('.//members')
                if members_el is not None:
                    members = [
                        {'value': m.get('value'), 'alias': m.get('alias')}
                        for m in members_el.findall('./member') if m.get('value') is not None
                    ]
                    if members:
                        param['allowable_values'] = members

                struct.parameters[internal_name] = param

    # Fields and Connections per datasource
    for ds in root.findall('.//datasource'):
        ds_name = ds.get('name')
        if ds_name == 'Parameters':
            continue

        ds_caption = ds.get('caption', ds_name)

        # Fields
        for col in ds.findall('.//column'):
            internal_name = col.get('name')
            if not internal_name:
                continue

            caption = col.get('caption', internal_name)
            datatype = col.get('datatype', 'string')
            role = col.get('role', 'dimension')
            calc = col.find('./calculation')
            formula = None
            if calc is not None:
                formula = calc.get('formula', '')

            aggregation = col.get('aggregation')
            number_format = col.get('default-format') or col.get('numeric-format')
            comment = _parse_field_comment(col)

            struct.fields[internal_name] = {
                'caption': caption,
                'datatype': datatype,
                'role': role,
                'ds': ds_caption,
                'formula': formula,
                'aggregation': aggregation,
                'format': number_format,
                'comment': comment
            }

            # Groups / value-list Sets: a <groupfilter> as a DIRECT child of
            # <column> (not nested under <filter>, which is the unrelated
            # filter-condition case handled elsewhere) is Tableau's shape
            # for both Groups and manual/value-list Sets - see the
            # WorkbookStructure.groups comment for why these share one bucket.
            direct_gf = col.find('./groupfilter')
            if direct_gf is not None:
                members = sorted({
                    gf.get('member') for gf in col.findall('.//groupfilter')
                    if gf.get('member')
                })
                struct.groups[internal_name] = {
                    'caption': caption,
                    'members': members,
                    'ds': ds_caption,
                }

            # Computed/condition Sets - see WorkbookStructure.computed_sets
            # comment on confidence level.
            set_calc = col.find("./calculation[@class='tableau-app:set-computed']")
            if set_calc is not None:
                struct.computed_sets[internal_name] = {
                    'caption': caption,
                    'formula': set_calc.get('formula'),
                    'ds': ds_caption,
                }

        # Connections (exclude federated and hyper/extract)
        conns = []
        for conn in ds.findall('.//connection'):
            conn_class = conn.get('class', '')
            # Skip federated wrapper and extract caches
            if conn_class in ('federated', 'hyper') or conn.get('dbname', '').startswith('extract'):
                continue
            if conn.get('filename', '').startswith('extract'):
                continue

            conn_attrs = {}
            for attr in ['class', 'server', 'port', 'dbname', 'schema', 'service', 'warehouse', 'filename', 'directory', 'username']:
                val = conn.get(attr)
                if val:
                    conn_attrs[attr] = val

            if conn_attrs:
                conns.append(conn_attrs)

        if conns:
            if ds_caption not in struct.connections:
                struct.connections[ds_caption] = []
            struct.connections[ds_caption].extend(conns)

        # Datasource-level filters (row-level conditions, not tied to a worksheet)
        ds_filters = _parse_filter_map(ds)
        if ds_filters:
            struct.datasource_filters.setdefault(ds_caption, {}).update(ds_filters)

    # Filters + visuals + filter-card visibility per worksheet
    for ws in root.findall('.//worksheet'):
        ws_name = ws.get('name')
        if not ws_name:
            continue

        filt_map = _parse_filter_map(ws)
        if filt_map:
            struct.filters[ws_name] = filt_map

        visuals = _parse_worksheet_visuals(ws)
        if visuals:
            struct.visuals[ws_name] = visuals

        filter_cards = _parse_filter_cards(ws)
        if filter_cards:
            struct.filter_cards[ws_name] = filter_cards

    # Dashboard Actions (filter/highlight/URL/parameter) - top-level
    # <actions>/<action> elements, keyed by name. The nested element naming
    # the action's kind is matched against a known-tag allowlist rather than
    # blindly taking the first child, so an unrelated child (e.g. a comment)
    # can't masquerade as the type; degrades to 'unknown' type otherwise.
    _ACTION_TYPE_TAGS = ('filter', 'highlight', 'url', 'parameter', 'go-to-sheet', 'set')
    for action in root.findall('.//actions/action'):
        name = action.get('name')
        if not name:
            continue
        action_type = None
        for child in action:
            if child.tag in _ACTION_TYPE_TAGS:
                action_type = child.tag
                break
        struct.actions[name] = {'type': action_type}

    # Hidden sheets: a worksheet with no corresponding <windows>/<window
    # class="worksheet"> entry has no visible tab and is effectively hidden
    # from end users, even though the sheet/view definition itself still
    # exists. Only computed when a <windows> section is present at all -
    # its absence means "unknown", not "everything is hidden", so
    # sheet_windows_known gates whether this is ever compared downstream.
    windows_el = root.find('.//windows')
    if windows_el is not None:
        struct.sheet_windows_known = True
        visible_worksheet_windows = {
            w.get('name') for w in windows_el.findall("./window[@class='worksheet']")
            if w.get('name')
        }
        for ws_name in struct.worksheets:
            if ws_name not in visible_worksheet_windows:
                struct.hidden_sheets.add(ws_name)

    return struct


@dataclass
class DiffItem:
    """A single diff item (add/remove/change)."""
    op: str  # 'add', 'remove', 'change'
    label: str
    before: Optional[str] = None
    after: Optional[str] = None
    parts: list = field(default_factory=list)  # [{attr, before, after}, ...]


@dataclass
class DiffGroup:
    """A grouped set of diffs."""
    key: str
    title: str
    items: list = field(default_factory=list)


@dataclass
class WorkbookDiff:
    """Full structural diff between two workbooks."""
    groups: list = field(default_factory=list)  # [DiffGroup, ...]
    counts: dict = field(default_factory=dict)
    total: int = 0


def _fmt(v) -> str:
    """Render a filter-detail/visual-detail value as a plain display string
    so DiffItem.before/after/parts[].before/after are always strings, never
    a raw list/dict/bool/set that the template/PDF would str()-repr ugly."""
    if v is None:
        return '(none)'
    if isinstance(v, bool):
        return 'yes' if v else 'no'
    if isinstance(v, dict):
        return ', '.join(f"{k}={_fmt(vv)}" for k, vv in v.items()) or '(none)'
    if isinstance(v, (list, tuple)):
        if not v:
            return '(none)'
        if all(isinstance(x, dict) for x in v):
            parts = []
            for d in v:
                if 'encoding' in d:
                    parts.append(f"{d.get('encoding')}: {d.get('field')}")
                elif 'field' in d:
                    parts.append(f"{d.get('field')} {d.get('direction') or ''}".strip())
                else:
                    parts.append(', '.join(f"{k}={val}" for k, val in d.items()))
            return ', '.join(parts)
        return ', '.join(str(x) for x in v)
    return str(v)


def _diff_filter_maps(pub_maps: dict, cand_maps: dict, noun: str) -> list:
    """Shared add/remove/change diffing for {key: {field_key: filter_detail}}
    maps - used for both worksheet-level `filters` and `datasource_filters`.
    `noun` e.g. 'Filter on' / 'Datasource filter on'."""
    items = []
    pub_keys = set(pub_maps.keys())
    cand_keys = set(cand_maps.keys())

    for k in cand_keys - pub_keys:
        fields_str = ', '.join(sorted(cand_maps[k].keys()))
        items.append(DiffItem(op='add', label=f"{noun} {k}: {fields_str}"))

    for k in pub_keys - cand_keys:
        fields_str = ', '.join(sorted(pub_maps[k].keys()))
        items.append(DiffItem(op='remove', label=f"{noun} {k}: {fields_str}"))

    for k in pub_keys & cand_keys:
        pub_fmap, cand_fmap = pub_maps[k], cand_maps[k]
        pub_fields, cand_fields = set(pub_fmap.keys()), set(cand_fmap.keys())

        if pub_fields != cand_fields:
            items.append(DiffItem(
                op='change', label=f"{noun} {k}",
                before=', '.join(sorted(pub_fields)) or '(none)',
                after=', '.join(sorted(cand_fields)) or '(none)'
            ))

        for field_key in pub_fields & cand_fields:
            pdet, cdet = pub_fmap[field_key], cand_fmap[field_key]
            if pdet == cdet:
                continue
            parts = []
            for attr in ('class', 'members', 'exclude', 'min', 'max', 'context',
                         'period_type', 'first_period', 'last_period',
                         'include_future', 'include_null'):
                pv, cv = pdet.get(attr), cdet.get(attr)
                if pv != cv:
                    parts.append({'attr': attr, 'before': _fmt(pv), 'after': _fmt(cv)})
            if parts:
                items.append(DiffItem(op='change', label=f"{noun.split()[0]} condition: {k} — {field_key}", parts=parts))
    return items


def compute_diff(published: WorkbookStructure, candidate: WorkbookStructure) -> WorkbookDiff:
    """Compute structural diff between published and candidate workbooks."""
    diff = WorkbookDiff()
    groups = {}

    # Views (worksheets + dashboards)
    view_items = []
    pub_views = published.worksheets | published.dashboards
    cand_views = candidate.worksheets | candidate.dashboards

    for v in cand_views - pub_views:
        view_items.append(DiffItem(op='add', label=f"View: {v}"))

    for v in pub_views - cand_views:
        view_items.append(DiffItem(op='remove', label=f"View: {v}"))

    # Check for type changes (sheet -> dashboard or vice versa)
    for v in pub_views & cand_views:
        pub_type = 'dashboard' if v in published.dashboards else 'sheet'
        cand_type = 'dashboard' if v in candidate.dashboards else 'sheet'
        if pub_type != cand_type:
            view_items.append(DiffItem(
                op='change',
                label=f"View: {v}",
                before=pub_type,
                after=cand_type
            ))

    if view_items:
        groups['views'] = DiffGroup(key='views', title='Views', items=view_items)

    # Fields
    field_items = []
    pub_field_keys = set(published.fields.keys())
    cand_field_keys = set(candidate.fields.keys())

    for fk in cand_field_keys - pub_field_keys:
        f = candidate.fields[fk]
        label = f"Field: {fk} ({f['datatype']}, {'calc' if f['formula'] else 'native'})"
        field_items.append(DiffItem(op='add', label=label))

    for fk in pub_field_keys - cand_field_keys:
        f = published.fields[fk]
        label = f"Field: {fk} ({f['datatype']}, {'calc' if f['formula'] else 'native'})"
        field_items.append(DiffItem(op='remove', label=label))

    # Field changes (datatype, role, caption)
    for fk in pub_field_keys & cand_field_keys:
        pf = published.fields[fk]
        cf = candidate.fields[fk]
        parts = []

        if pf['datatype'] != cf['datatype']:
            parts.append({'attr': 'datatype', 'before': pf['datatype'], 'after': cf['datatype']})
        if pf['role'] != cf['role']:
            parts.append({'attr': 'role', 'before': pf['role'], 'after': cf['role']})
        if pf['caption'] != cf['caption']:
            parts.append({'attr': 'caption', 'before': pf['caption'], 'after': cf['caption']})
        if pf.get('aggregation') != cf.get('aggregation'):
            parts.append({'attr': 'aggregation', 'before': pf.get('aggregation') or '(none)', 'after': cf.get('aggregation') or '(none)'})
        if pf.get('format') != cf.get('format'):
            parts.append({'attr': 'format', 'before': pf.get('format') or '(none)', 'after': cf.get('format') or '(none)'})
        if pf.get('comment') != cf.get('comment'):
            parts.append({'attr': 'comment', 'before': pf.get('comment') or '(none)', 'after': cf.get('comment') or '(none)'})

        if parts:
            field_items.append(DiffItem(
                op='change',
                label=f"Field: {fk}",
                parts=parts
            ))

    if field_items:
        groups['fields'] = DiffGroup(key='fields', title='Fields', items=field_items)

    # Calculated fields
    calc_items = []
    pub_calcs = {k: v for k, v in published.fields.items() if v['formula']}
    cand_calcs = {k: v for k, v in candidate.fields.items() if v['formula']}

    for ck in cand_calcs.keys() - pub_calcs.keys():
        label = f"Calculation: {ck}"
        calc_items.append(DiffItem(op='add', label=label))

    for ck in pub_calcs.keys() - cand_calcs.keys():
        label = f"Calculation: {ck}"
        calc_items.append(DiffItem(op='remove', label=label))

    for ck in pub_calcs.keys() & cand_calcs.keys():
        if pub_calcs[ck]['formula'] != cand_calcs[ck]['formula']:
            calc_items.append(DiffItem(
                op='change',
                label=f"Calculation: {ck}",
                before=pub_calcs[ck]['formula'],
                after=cand_calcs[ck]['formula']
            ))

    if calc_items:
        groups['calcs'] = DiffGroup(key='calcs', title='Calculated Fields', items=calc_items)

    # Parameters
    param_items = []
    pub_param_keys = set(published.parameters.keys())
    cand_param_keys = set(candidate.parameters.keys())

    for pk in cand_param_keys - pub_param_keys:
        p = candidate.parameters[pk]
        param_items.append(DiffItem(op='add', label=f"Parameter: {pk}"))

    for pk in pub_param_keys - cand_param_keys:
        p = published.parameters[pk]
        param_items.append(DiffItem(op='remove', label=f"Parameter: {pk}"))

    for pk in pub_param_keys & cand_param_keys:
        pp = published.parameters[pk]
        cp = candidate.parameters[pk]
        parts = []

        if pp['datatype'] != cp['datatype']:
            parts.append({'attr': 'datatype', 'before': pp['datatype'], 'after': cp['datatype']})
        if pp['caption'] != cp['caption']:
            parts.append({'attr': 'caption', 'before': pp['caption'], 'after': cp['caption']})
        if pp['value'] != cp['value']:
            parts.append({'attr': 'value', 'before': pp['value'] or 'null', 'after': cp['value'] or 'null'})
        if pp.get('range') != cp.get('range'):
            parts.append({'attr': 'range', 'before': _fmt(pp.get('range')), 'after': _fmt(cp.get('range'))})
        if pp.get('allowable_values') != cp.get('allowable_values'):
            parts.append({'attr': 'allowable_values', 'before': _fmt(pp.get('allowable_values')), 'after': _fmt(cp.get('allowable_values'))})

        if parts:
            param_items.append(DiffItem(op='change', label=f"Parameter: {pk}", parts=parts))

    if param_items:
        groups['params'] = DiffGroup(key='params', title='Parameters', items=param_items)

    # Filters (worksheet-level) + Datasource Filters - share the same
    # add/remove/change-membership/change-condition logic
    filter_items = _diff_filter_maps(published.filters, candidate.filters, 'Filter on')
    if filter_items:
        groups['filters'] = DiffGroup(key='filters', title='Filters', items=filter_items)

    ds_filter_items = _diff_filter_maps(published.datasource_filters, candidate.datasource_filters, 'Datasource filter on')
    if ds_filter_items:
        groups['ds_filters'] = DiffGroup(key='ds_filters', title='Datasource Filters', items=ds_filter_items)

    # Worksheet visuals (mark type, encodings, sort order) - only for
    # worksheets present in BOTH workbooks; new/removed worksheets are
    # already covered by the 'views' group above.
    visual_items = []
    for ws in published.worksheets & candidate.worksheets:
        pv = published.visuals.get(ws, {})
        cv = candidate.visuals.get(ws, {})
        if pv == cv:
            continue
        parts = []
        for attr in ('marks', 'encodings', 'sorts'):
            pav, cav = pv.get(attr), cv.get(attr)
            if pav != cav:
                parts.append({'attr': attr, 'before': _fmt(pav), 'after': _fmt(cav)})
        if parts:
            visual_items.append(DiffItem(op='change', label=f"Worksheet visual: {ws}", parts=parts))
    if visual_items:
        groups['visuals'] = DiffGroup(key='visuals', title='Worksheet Visuals', items=visual_items)

    # Dashboard layout (zone add/remove by type+name identity) - only for
    # dashboards present in both; new/removed dashboards already covered
    # by the 'views' group. Position/size (x/y/w/h) deliberately not
    # diffed - those jitter on every save and would be pure noise.
    zone_items = []
    for db_name in published.dashboards & candidate.dashboards:
        pub_z = published.dash_zones.get(db_name, {})
        cand_z = candidate.dash_zones.get(db_name, {})
        for zk in set(cand_z) - set(pub_z):
            z = cand_z[zk]
            zone_items.append(DiffItem(op='add', label=f"Dashboard zone: {db_name} — {z['name']} ({z.get('type') or 'worksheet/object'})"))
        for zk in set(pub_z) - set(cand_z):
            z = pub_z[zk]
            zone_items.append(DiffItem(op='remove', label=f"Dashboard zone: {db_name} — {z['name']} ({z.get('type') or 'worksheet/object'})"))
    if zone_items:
        groups['dash_layout'] = DiffGroup(key='dash_layout', title='Dashboard Layout', items=zone_items)

    # Connections ("Data sources")
    conn_items = []
    pub_conn_keys = set(published.connections.keys())
    cand_conn_keys = set(candidate.connections.keys())

    for ck in cand_conn_keys - pub_conn_keys:
        conn_items.append(DiffItem(op='add', label=f"Data Source: {ck}"))

    for ck in pub_conn_keys - cand_conn_keys:
        conn_items.append(DiffItem(op='remove', label=f"Data Source: {ck}"))

    for ck in pub_conn_keys & cand_conn_keys:
        pub_conns = published.connections[ck]
        cand_conns = candidate.connections[ck]

        if len(pub_conns) != len(cand_conns):
            conn_items.append(DiffItem(
                op='change',
                label=f"Data Source: {ck}",
                before=f"{len(pub_conns)} connection(s)",
                after=f"{len(cand_conns)} connection(s)"
            ))
        else:
            for i, (pc, cc) in enumerate(zip(pub_conns, cand_conns)):
                parts = []
                for attr in ['class', 'server', 'port', 'dbname', 'schema', 'service', 'warehouse', 'filename', 'directory', 'username']:
                    if pc.get(attr) != cc.get(attr):
                        parts.append({
                            'attr': attr,
                            'before': pc.get(attr, ''),
                            'after': cc.get(attr, '')
                        })

                if parts:
                    conn_items.append(DiffItem(
                        op='change',
                        label=f"Data Source: {ck} (connection {i+1})",
                        parts=parts
                    ))

    if conn_items:
        groups['conns'] = DiffGroup(key='conns', title='Data Sources', items=conn_items)

    # Groups & Sets (value-list) - see WorkbookStructure.groups comment
    group_items = []
    pub_group_keys = set(published.groups.keys())
    cand_group_keys = set(candidate.groups.keys())

    for gk in cand_group_keys - pub_group_keys:
        group_items.append(DiffItem(op='add', label=f"Group/Set: {gk}"))
    for gk in pub_group_keys - cand_group_keys:
        group_items.append(DiffItem(op='remove', label=f"Group/Set: {gk}"))
    for gk in pub_group_keys & cand_group_keys:
        pg, cg = published.groups[gk], candidate.groups[gk]
        parts = []
        if pg.get('caption') != cg.get('caption'):
            parts.append({'attr': 'caption', 'before': pg.get('caption'), 'after': cg.get('caption')})
        if pg.get('members') != cg.get('members'):
            parts.append({'attr': 'members', 'before': _fmt(pg.get('members')), 'after': _fmt(cg.get('members'))})
        if parts:
            group_items.append(DiffItem(op='change', label=f"Group/Set: {gk}", parts=parts))

    if group_items:
        groups['groups'] = DiffGroup(key='groups', title='Groups & Sets', items=group_items)

    # Computed/condition Sets - best-effort, see WorkbookStructure.computed_sets
    set_items = []
    pub_set_keys = set(published.computed_sets.keys())
    cand_set_keys = set(candidate.computed_sets.keys())

    for sk in cand_set_keys - pub_set_keys:
        set_items.append(DiffItem(op='add', label=f"Computed set: {sk}"))
    for sk in pub_set_keys - cand_set_keys:
        set_items.append(DiffItem(op='remove', label=f"Computed set: {sk}"))
    for sk in pub_set_keys & cand_set_keys:
        ps, cs = published.computed_sets[sk], candidate.computed_sets[sk]
        if ps.get('formula') != cs.get('formula'):
            set_items.append(DiffItem(
                op='change', label=f"Computed set: {sk}",
                before=ps.get('formula'), after=cs.get('formula')
            ))

    if set_items:
        groups['computed_sets'] = DiffGroup(key='computed_sets', title='Computed Sets', items=set_items)

    # Dashboard Actions
    action_items = []
    pub_action_keys = set(published.actions.keys())
    cand_action_keys = set(candidate.actions.keys())

    for ak in cand_action_keys - pub_action_keys:
        action_items.append(DiffItem(op='add', label=f"Action: {ak}"))
    for ak in pub_action_keys - cand_action_keys:
        action_items.append(DiffItem(op='remove', label=f"Action: {ak}"))
    for ak in pub_action_keys & cand_action_keys:
        pa, ca = published.actions[ak], candidate.actions[ak]
        if pa.get('type') != ca.get('type'):
            action_items.append(DiffItem(
                op='change', label=f"Action: {ak}",
                before=pa.get('type') or '(unknown)', after=ca.get('type') or '(unknown)'
            ))

    if action_items:
        groups['actions'] = DiffGroup(key='actions', title='Actions', items=action_items)

    # Sheet visibility (hidden/unhidden) - only meaningful when both sides
    # actually carry window-state info; see WorkbookStructure.sheet_windows_known.
    visibility_items = []
    if published.sheet_windows_known and candidate.sheet_windows_known:
        for ws in published.worksheets & candidate.worksheets:
            was_hidden = ws in published.hidden_sheets
            is_hidden = ws in candidate.hidden_sheets
            if was_hidden != is_hidden:
                visibility_items.append(DiffItem(
                    op='change', label=f"Sheet visibility: {ws}",
                    before='Hidden' if was_hidden else 'Visible',
                    after='Hidden' if is_hidden else 'Visible'
                ))

    if visibility_items:
        groups['sheet_visibility'] = DiffGroup(key='sheet_visibility', title='Sheet Visibility', items=visibility_items)

    # Quick filter card visibility (<slices>) - distinct from the <filter>
    # condition itself; only diffed for worksheets present in both.
    filter_card_items = []
    for ws in published.worksheets & candidate.worksheets:
        pub_cards = set(published.filter_cards.get(ws, {}).keys())
        cand_cards = set(candidate.filter_cards.get(ws, {}).keys())
        for fk in cand_cards - pub_cards:
            filter_card_items.append(DiffItem(op='add', label=f"Quick filter card shown: {ws} — {fk}"))
        for fk in pub_cards - cand_cards:
            filter_card_items.append(DiffItem(op='remove', label=f"Quick filter card hidden: {ws} — {fk}"))

    if filter_card_items:
        groups['filter_cards'] = DiffGroup(key='filter_cards', title='Quick Filter Cards', items=filter_card_items)

    # Build final diff
    diff.groups = list(groups.values())
    diff.counts = {
        'views_added': len([i for g in groups.values() if g.key == 'views' for i in g.items if i.op == 'add']),
        'views_removed': len([i for g in groups.values() if g.key == 'views' for i in g.items if i.op == 'remove']),
        'fields_added': len([i for g in groups.values() if g.key == 'fields' for i in g.items if i.op == 'add']),
        'fields_removed': len([i for g in groups.values() if g.key == 'fields' for i in g.items if i.op == 'remove']),
        'fields_changed': len([i for g in groups.values() if g.key == 'fields' for i in g.items if i.op == 'change']),
        'calcs_added': len([i for g in groups.values() if g.key == 'calcs' for i in g.items if i.op == 'add']),
        'calcs_removed': len([i for g in groups.values() if g.key == 'calcs' for i in g.items if i.op == 'remove']),
        'calcs_changed': len([i for g in groups.values() if g.key == 'calcs' for i in g.items if i.op == 'change']),
        'params_added': len([i for g in groups.values() if g.key == 'params' for i in g.items if i.op == 'add']),
        'params_removed': len([i for g in groups.values() if g.key == 'params' for i in g.items if i.op == 'remove']),
        'params_changed': len([i for g in groups.values() if g.key == 'params' for i in g.items if i.op == 'change']),
        'filters_added': len([i for g in groups.values() if g.key == 'filters' for i in g.items if i.op == 'add']),
        'filters_removed': len([i for g in groups.values() if g.key == 'filters' for i in g.items if i.op == 'remove']),
        'filters_changed': len([i for g in groups.values() if g.key == 'filters' for i in g.items if i.op == 'change']),
        'ds_filters_added': len([i for g in groups.values() if g.key == 'ds_filters' for i in g.items if i.op == 'add']),
        'ds_filters_removed': len([i for g in groups.values() if g.key == 'ds_filters' for i in g.items if i.op == 'remove']),
        'ds_filters_changed': len([i for g in groups.values() if g.key == 'ds_filters' for i in g.items if i.op == 'change']),
        'ds_added': len([i for g in groups.values() if g.key == 'conns' for i in g.items if i.op == 'add']),
        'ds_removed': len([i for g in groups.values() if g.key == 'conns' for i in g.items if i.op == 'remove']),
        'conns_changed': len([i for g in groups.values() if g.key == 'conns' for i in g.items if i.op == 'change']),
        'visuals_changed': len([i for g in groups.values() if g.key == 'visuals' for i in g.items if i.op == 'change']),
        'dash_zones_added': len([i for g in groups.values() if g.key == 'dash_layout' for i in g.items if i.op == 'add']),
        'dash_zones_removed': len([i for g in groups.values() if g.key == 'dash_layout' for i in g.items if i.op == 'remove']),
        'groups_added': len([i for g in groups.values() if g.key == 'groups' for i in g.items if i.op == 'add']),
        'groups_removed': len([i for g in groups.values() if g.key == 'groups' for i in g.items if i.op == 'remove']),
        'groups_changed': len([i for g in groups.values() if g.key == 'groups' for i in g.items if i.op == 'change']),
        'computed_sets_added': len([i for g in groups.values() if g.key == 'computed_sets' for i in g.items if i.op == 'add']),
        'computed_sets_removed': len([i for g in groups.values() if g.key == 'computed_sets' for i in g.items if i.op == 'remove']),
        'computed_sets_changed': len([i for g in groups.values() if g.key == 'computed_sets' for i in g.items if i.op == 'change']),
        'actions_added': len([i for g in groups.values() if g.key == 'actions' for i in g.items if i.op == 'add']),
        'actions_removed': len([i for g in groups.values() if g.key == 'actions' for i in g.items if i.op == 'remove']),
        'actions_changed': len([i for g in groups.values() if g.key == 'actions' for i in g.items if i.op == 'change']),
        'sheet_visibility_changed': len([i for g in groups.values() if g.key == 'sheet_visibility' for i in g.items if i.op == 'change']),
        'filter_cards_added': len([i for g in groups.values() if g.key == 'filter_cards' for i in g.items if i.op == 'add']),
        'filter_cards_removed': len([i for g in groups.values() if g.key == 'filter_cards' for i in g.items if i.op == 'remove']),
    }

    diff.total = sum(len(g.items) for g in diff.groups)

    return diff


def classify_custom_view_impact(published: WorkbookStructure, candidate: WorkbookStructure, diff: WorkbookDiff) -> dict:
    """Classify impact level for custom view notifications.

    Returns: {
        'risk_level': 'None' | 'Low' | 'Medium' | 'High',
        'findings': [{'category', 'severity', 'title', 'detail', 'sheets'}, ...],
        'classification': 'directly_impacted' | 'potentially_impacted' | 'label_change_only' | 'no_change'
    }
    """
    findings = []
    risk_level = 'None'

    # Removed/renamed views
    pub_views = published.worksheets | published.dashboards
    cand_views = candidate.worksheets | candidate.dashboards

    for v in pub_views - cand_views:
        findings.append({
            'category': 'Views',
            'severity': 'High',
            'title': f'Base view "{v}" removed or renamed',
            'detail': 'Custom views tied to this view will be orphaned or unloadable.',
            'sheets': [v]
        })

    # Removed fields
    for fk in set(published.fields.keys()) - set(candidate.fields.keys()):
        f = published.fields[fk]
        findings.append({
            'category': 'Fields',
            'severity': 'High',
            'title': f'Field "{fk}" removed',
            'detail': 'Stored filters/sorts using this field will reset.',
            'sheets': []
        })

    # Changed field datatypes
    for fk in set(published.fields.keys()) & set(candidate.fields.keys()):
        pf = published.fields[fk]
        cf = candidate.fields[fk]
        if pf['datatype'] != cf['datatype']:
            findings.append({
                'category': 'Fields',
                'severity': 'Medium',
                'title': f'Field "{fk}" datatype changed',
                'detail': f"{pf['datatype']} → {cf['datatype']}. Stored filter values may become invalid.",
                'sheets': []
            })

    # Changed field roles (dimension <-> measure) - shelf placement, sorts,
    # and legend state built against the old role may not survive reload,
    # even though the field itself still resolves.
    for fk in set(published.fields.keys()) & set(candidate.fields.keys()):
        pf = published.fields[fk]
        cf = candidate.fields[fk]
        if pf['role'] != cf['role']:
            findings.append({
                'category': 'Fields',
                'severity': 'Medium',
                'title': f'Field "{fk}" role changed',
                'detail': f"{pf['role']} → {cf['role']}. Shelf placement, sorts, and legend state built on the old role may not survive reload.",
                'sheets': []
            })

    # Changed field captions
    for fk in set(published.fields.keys()) & set(candidate.fields.keys()):
        pf = published.fields[fk]
        cf = candidate.fields[fk]
        if pf['caption'] != cf['caption']:
            findings.append({
                'category': 'Fields',
                'severity': 'Medium',
                'title': f'Field "{fk}" caption changed',
                'detail': f'"{pf["caption"]}" → "{cf["caption"]}". Labels shift, but references still work.',
                'sheets': []
            })

    # Data source / connection changes - fully diffed in compute_diff's
    # 'conns' group but never scored for custom-view impact until now. A
    # live connection swap (server/dbname/credentials) or an embedded<->
    # published conversion can silently repoint the workbook at different
    # data, which breaks saved custom view state even though no field or
    # filter identity technically changed.
    for ck in set(published.connections.keys()) & set(candidate.connections.keys()):
        if published.connections[ck] != candidate.connections[ck]:
            findings.append({
                'category': 'Data Sources',
                'severity': 'High',
                'title': f'Data source "{ck}" connection changed',
                'detail': 'Server, database, or credentials changed for this data source. Custom views may silently show different data, or fail to load if the new connection is unreachable.',
                'sheets': []
            })

    for ck in set(published.connections.keys()) - set(candidate.connections.keys()):
        findings.append({
            'category': 'Data Sources',
            'severity': 'High',
            'title': f'Data source "{ck}" live connection info disappeared',
            'detail': 'No live (non-extract) connection was found for this data source anymore - possibly converted to extract-only, or its connection type changed. Custom views may fail to load or refresh.',
            'sheets': []
        })

    # Datasource-level (row-level security) filter changes - fully diffed in
    # compute_diff's 'ds_filters' group but never scored until now. RLS
    # filters gate what rows a saved custom view can even see, so any
    # change here is treated as High regardless of whether it's an
    # addition, removal, or condition edit - unlike worksheet-level
    # filters, there's no "cosmetic" version of an RLS change.
    for dsk in set(published.datasource_filters.keys()) - set(candidate.datasource_filters.keys()):
        findings.append({
            'category': 'Data Source Filters',
            'severity': 'High',
            'title': f'Row-level security filter removed from "{dsk}"',
            'detail': 'All row-level filters on this data source were removed. Users may now see rows a saved custom view previously excluded.',
            'sheets': []
        })

    for dsk in set(candidate.datasource_filters.keys()) - set(published.datasource_filters.keys()):
        findings.append({
            'category': 'Data Source Filters',
            'severity': 'High',
            'title': f'Row-level security filter added to "{dsk}"',
            'detail': 'A new row-level filter now applies to this data source. A saved custom view may unexpectedly show less data, or none at all.',
            'sheets': []
        })

    for dsk in set(published.datasource_filters.keys()) & set(candidate.datasource_filters.keys()):
        if published.datasource_filters[dsk] != candidate.datasource_filters[dsk]:
            findings.append({
                'category': 'Data Source Filters',
                'severity': 'High',
                'title': f'Row-level security filter changed on "{dsk}"',
                'detail': 'Condition, membership, or scope of a row-level filter changed on this data source. Saved custom views may see a different row set than before.',
                'sheets': []
            })

    # Removed parameters
    for pk in set(published.parameters.keys()) - set(candidate.parameters.keys()):
        findings.append({
            'category': 'Parameters',
            'severity': 'High',
            'title': f'Parameter "{pk}" removed',
            'detail': 'Saved parameter selections in custom views will reset.',
            'sheets': []
        })

    # Changed parameters (type/value/domain) and caption renames, checked
    # independently so a caption-only rename doesn't get lumped in with a
    # type/value/domain change - the latter is what actually risks
    # invalidating a saved selection.
    for pk in set(published.parameters.keys()) & set(candidate.parameters.keys()):
        pp = published.parameters[pk]
        cp = candidate.parameters[pk]
        if (pp['datatype'] != cp['datatype'] or pp['value'] != cp['value']
                or pp.get('range') != cp.get('range')
                or pp.get('allowable_values') != cp.get('allowable_values')):
            findings.append({
                'category': 'Parameters',
                'severity': 'Medium',
                'title': f'Parameter "{pk}" changed',
                'detail': 'Type, default value, or allowable-values domain updated. May invalidate saved selections.',
                'sheets': []
            })
        if pp['caption'] != cp['caption']:
            findings.append({
                'category': 'Parameters',
                'severity': 'Medium',
                'title': f'Parameter "{pk}" caption changed',
                'detail': f'"{pp["caption"]}" → "{cp["caption"]}". The control label changes, but stored selections still resolve since the internal identity is unchanged.',
                'sheets': []
            })

    # New parameter added (low severity, informational) - mirrors "new view
    # added" below; nothing depends on it yet.
    for pk in set(candidate.parameters.keys()) - set(published.parameters.keys()):
        findings.append({
            'category': 'Parameters',
            'severity': 'Low',
            'title': f'New parameter "{pk}" added',
            'detail': 'Nothing depends on this yet.',
            'sheets': []
        })

    # Filters whose underlying field was removed (checked per-field, not just
    # "sheet lost all its filters" - a sheet can keep some filters while one
    # specific filtered field disappears).
    removed_field_keys = set(published.fields.keys()) - set(candidate.fields.keys())
    for ws, filt_map in published.filters.items():
        if ws not in cand_views:
            continue  # already covered by the base-view-removed finding above
        broken = [fk for fk in filt_map.keys() if fk in removed_field_keys]
        if broken:
            findings.append({
                'category': 'Filters',
                'severity': 'High',
                'title': f'Filter on removed field(s) in "{ws}"',
                'detail': f"{', '.join(broken)} removed; the saved filter selection will reset.",
                'sheets': [ws]
            })

    # Filter condition changed on a field that's still present (e.g. default
    # members/range narrowed or widened) - the saved custom-view selection
    # may no longer match or may silently reset to the new default.
    for ws in set(published.filters.keys()) & set(candidate.filters.keys()):
        if ws not in cand_views:
            continue
        pub_fmap, cand_fmap = published.filters[ws], candidate.filters[ws]
        changed = [
            fk for fk in set(pub_fmap) & set(cand_fmap)
            if fk not in removed_field_keys and pub_fmap[fk] != cand_fmap[fk]
        ]
        if changed:
            findings.append({
                'category': 'Filters',
                'severity': 'Medium',
                'title': f'Filter condition changed in "{ws}"',
                'detail': f"{', '.join(changed)}: default members/range/exclude changed. "
                          f"The saved custom-view filter selection may no longer match or may reset.",
                'sheets': [ws]
            })

    # Filter card removed from a worksheet while its field still exists
    # elsewhere (distinct from "filter on removed field" above - here the
    # field is fine, only the filter card itself was deleted).
    for ws in set(published.filters.keys()) & cand_views:
        pub_fmap = published.filters[ws]
        cand_fmap = candidate.filters.get(ws, {})
        dropped = [fk for fk in pub_fmap.keys() if fk not in cand_fmap and fk not in removed_field_keys]
        if dropped:
            findings.append({
                'category': 'Filters',
                'severity': 'Medium',
                'title': f'Filter card removed in "{ws}"',
                'detail': f"{', '.join(dropped)}: filter card deleted from the worksheet (field still exists). "
                          f"The saved custom-view filter selection for this filter will be dropped.",
                'sheets': [ws]
            })

    # New filter added to an existing worksheet - doesn't break saved state,
    # but a custom view's underlying data may not satisfy the new filter's
    # default condition and could silently render empty.
    for ws in set(candidate.filters.keys()) & pub_views & cand_views:
        cand_fmap = candidate.filters[ws]
        pub_fmap = published.filters.get(ws, {})
        added = [fk for fk in cand_fmap.keys() if fk not in pub_fmap]
        if added:
            findings.append({
                'category': 'Filters',
                'severity': 'Medium',
                'title': f'New filter added in "{ws}"',
                'detail': f"{', '.join(added)}: a new filter now applies to this view. If a saved custom "
                          f"view's underlying data doesn't satisfy this filter's default condition, it may "
                          f"unexpectedly show no data.",
                'sheets': [ws]
            })

    # Quick-filter-card visibility (<slices>) - distinct from the <filter>
    # condition itself: a card can be hidden ("Show Filter" turned off)
    # while the underlying filter condition is untouched, or vice versa.
    for ws in published.worksheets & candidate.worksheets:
        pub_cards = set(published.filter_cards.get(ws, {}).keys())
        cand_cards = set(candidate.filter_cards.get(ws, {}).keys())
        hidden = pub_cards - cand_cards
        if hidden:
            findings.append({
                'category': 'Filters',
                'severity': 'Medium',
                'title': f'Quick filter card hidden in "{ws}"',
                'detail': f"{', '.join(sorted(hidden))}: the filter card is no longer shown on the worksheet "
                          f"(its condition may still be applied). Users lose the ability to change the saved "
                          f"selection interactively.",
                'sheets': [ws]
            })
        shown = cand_cards - pub_cards
        if shown:
            findings.append({
                'category': 'Filters',
                'severity': 'Low',
                'title': f'Quick filter card shown in "{ws}"',
                'detail': f"{', '.join(sorted(shown))}: a new quick filter card is now visible on the worksheet.",
                'sheets': [ws]
            })

    # Worksheet visuals (mark type, sort order, shelf/encoding pills) - fully
    # diffed in compute_diff's 'visuals' group but never scored for
    # custom-view impact until now.
    for ws in published.worksheets & candidate.worksheets:
        pv = published.visuals.get(ws, {})
        cv = candidate.visuals.get(ws, {})

        if pv.get('marks') != cv.get('marks'):
            findings.append({
                'category': 'Marks',
                'severity': 'High',
                'title': f'Mark type changed in "{ws}"',
                'detail': f"{_fmt(pv.get('marks'))} → {_fmt(cv.get('marks'))}. Selected marks in a saved custom view can resolve differently or disappear entirely.",
                'sheets': [ws]
            })

        if pv.get('sorts') != cv.get('sorts'):
            findings.append({
                'category': 'Sorts',
                'severity': 'Low',
                'title': f'Sort order changed in "{ws}"',
                'detail': 'Saved custom view sort state may no longer match; sort may reset to the new default on next load.',
                'sheets': [ws]
            })

        if pv.get('encodings') != cv.get('encodings'):
            findings.append({
                'category': 'Encodings',
                'severity': 'Medium',
                'title': f'Shelf/encoding pills changed in "{ws}"',
                'detail': 'Fields on rows/columns/color/size/etc. changed. A saved custom view may not restore its exact pill layout.',
                'sheets': [ws]
            })

    # Calculated field's referenced-field set changes - a pure logic edit
    # that keeps referencing the same fields stays out of scope (matches this
    # classifier's "custom view state" scope, not "did the numbers change"),
    # but a calc that starts/stops referencing a different field is a proxy
    # for "stored filters/sorts on this calc may now behave differently."
    for fk in set(published.fields.keys()) & set(candidate.fields.keys()):
        pf = published.fields[fk]
        cf = candidate.fields[fk]
        if pf.get('formula') and cf.get('formula') and pf['formula'] != cf['formula']:
            pub_refs = _referenced_fields(pf['formula'])
            cand_refs = _referenced_fields(cf['formula'])
            if pub_refs != cand_refs:
                removed_refs = sorted(pub_refs - cand_refs)
                added_refs = sorted(cand_refs - pub_refs)
                findings.append({
                    'category': 'Calculations',
                    'severity': 'Medium',
                    'title': f'Calculation "{fk}" now references different fields',
                    'detail': f"Removed: {', '.join(removed_refs) or '(none)'}; added: {', '.join(added_refs) or '(none)'}. "
                              f"Downstream filters/sorts on this calc may behave differently even though the field itself still exists.",
                    'sheets': []
                })

    # Field default-aggregation changed - newly-placed pills pick up the new
    # default, but already-saved custom view pills are unaffected, hence Low
    # not Medium. Format/comment changes are deliberately excluded here:
    # purely cosmetic, zero risk to saved view state.
    for fk in set(published.fields.keys()) & set(candidate.fields.keys()):
        pf = published.fields[fk]
        cf = candidate.fields[fk]
        if pf.get('aggregation') != cf.get('aggregation'):
            findings.append({
                'category': 'Fields',
                'severity': 'Low',
                'title': f'Field "{fk}" default aggregation changed',
                'detail': f"{pf.get('aggregation') or '(none)'} → {cf.get('aggregation') or '(none)'}. "
                          f"Newly-placed pills will use the new default; already-saved custom view pills are unaffected.",
                'sheets': []
            })

    # Groups & Sets (value-list) - membership/removal changes. Not
    # distinguished from computed/condition Sets below since both share the
    # same <groupfilter> XML shape and aren't reliably distinguishable from
    # that element alone (see WorkbookStructure.groups).
    for gk in set(published.groups.keys()) - set(candidate.groups.keys()):
        findings.append({
            'category': 'Groups & Sets',
            'severity': 'High',
            'title': f'Group/Set "{gk}" removed',
            'detail': 'Saved filters/sorts referencing this group or set will reset or fail to resolve.',
            'sheets': []
        })
    for gk in set(published.groups.keys()) & set(candidate.groups.keys()):
        pg, cg = published.groups[gk], candidate.groups[gk]
        if pg.get('members') != cg.get('members'):
            findings.append({
                'category': 'Groups & Sets',
                'severity': 'Medium',
                'title': f'Group/Set "{gk}" membership changed',
                'detail': 'Which values belong to this group/set changed. A saved custom view filtered on it may include or exclude different data than before.',
                'sheets': []
            })
    for gk in set(candidate.groups.keys()) - set(published.groups.keys()):
        findings.append({
            'category': 'Groups & Sets',
            'severity': 'Low',
            'title': f'New Group/Set "{gk}" added',
            'detail': 'Nothing depends on this yet.',
            'sheets': []
        })

    # Computed/condition Sets - best-effort detection (see
    # WorkbookStructure.computed_sets); silently produces zero findings if
    # this workbook's sets don't use the matched calculation class.
    for sk in set(published.computed_sets.keys()) - set(candidate.computed_sets.keys()):
        findings.append({
            'category': 'Groups & Sets',
            'severity': 'High',
            'title': f'Computed set "{sk}" removed',
            'detail': 'Saved filters/sorts referencing this set will reset or fail to resolve.',
            'sheets': []
        })
    for sk in set(published.computed_sets.keys()) & set(candidate.computed_sets.keys()):
        ps, cs = published.computed_sets[sk], candidate.computed_sets[sk]
        if ps.get('formula') != cs.get('formula'):
            findings.append({
                'category': 'Groups & Sets',
                'severity': 'Medium',
                'title': f'Computed set "{sk}" condition changed',
                'detail': 'The membership rule for this set changed. A saved custom view filtered on it may include or exclude different data than before.',
                'sheets': []
            })
    for sk in set(candidate.computed_sets.keys()) - set(published.computed_sets.keys()):
        findings.append({
            'category': 'Groups & Sets',
            'severity': 'Low',
            'title': f'New computed set "{sk}" added',
            'detail': 'Nothing depends on this yet.',
            'sheets': []
        })

    # Dashboard Actions (filter/highlight/URL/parameter)
    for ak in set(published.actions.keys()) - set(candidate.actions.keys()):
        findings.append({
            'category': 'Actions',
            'severity': 'Medium',
            'title': f'Action "{ak}" removed',
            'detail': 'Interactive behavior (filter/highlight/URL/parameter action) tied to this action is gone. Dashboards relying on it will lose that interaction.',
            'sheets': []
        })
    for ak in set(published.actions.keys()) & set(candidate.actions.keys()):
        pa, ca = published.actions[ak], candidate.actions[ak]
        if pa.get('type') != ca.get('type'):
            findings.append({
                'category': 'Actions',
                'severity': 'Medium',
                'title': f'Action "{ak}" type changed',
                'detail': f"{pa.get('type') or '(unknown)'} → {ca.get('type') or '(unknown)'}. The interaction this action drives has changed.",
                'sheets': []
            })
    for ak in set(candidate.actions.keys()) - set(published.actions.keys()):
        findings.append({
            'category': 'Actions',
            'severity': 'Low',
            'title': f'New action "{ak}" added',
            'detail': 'Nothing depends on this yet.',
            'sheets': []
        })

    # Hidden-sheet transitions - only evaluated when both workbooks actually
    # carry window-state info (see WorkbookStructure.sheet_windows_known);
    # otherwise this is left unknown rather than guessed.
    if published.sheet_windows_known and candidate.sheet_windows_known:
        for ws in published.worksheets & candidate.worksheets:
            was_hidden = ws in published.hidden_sheets
            is_hidden = ws in candidate.hidden_sheets
            if is_hidden and not was_hidden:
                findings.append({
                    'category': 'Sheet Visibility',
                    'severity': 'High',
                    'title': f'Sheet "{ws}" is now hidden',
                    'detail': 'Hiding a sheet breaks custom views tied to it, even if it is later unhidden.',
                    'sheets': [ws]
                })
            elif was_hidden and not is_hidden:
                findings.append({
                    'category': 'Sheet Visibility',
                    'severity': 'Low',
                    'title': f'Sheet "{ws}" is now visible',
                    'detail': 'This sheet regained a visible tab.',
                    'sheets': [ws]
                })

    # Added views (low severity, informational)
    for v in cand_views - pub_views:
        findings.append({
            'category': 'Views',
            'severity': 'Low',
            'title': f'New view "{v}" added',
            'detail': 'Nothing depends on this yet.',
            'sheets': [v]
        })

    # Determine risk level and classification
    if findings:
        severities = {f['severity'] for f in findings}
        if 'High' in severities:
            risk_level = 'High'
        elif 'Medium' in severities:
            risk_level = 'Medium'
        else:
            risk_level = 'Low'

    # Determine classification for custom views. Caption-only changes are tagged
    # Medium severity above (for the findings list, per spec), but that means
    # risk_level is never 'Low' when they're present - so "label change only"
    # must be checked independent of risk_level, or it can never be reached.
    all_caption_only = bool(findings) and all('caption' in f['title'].lower() for f in findings)

    classification = 'no_change'
    if risk_level == 'High':
        # Check if it's specifically a view removal/rename
        view_findings = [f for f in findings if f['category'] == 'Views' and f['severity'] == 'High']
        classification = 'directly_impacted' if view_findings else 'potentially_impacted'
    elif all_caption_only:
        classification = 'label_change_only'
    elif findings:
        classification = 'potentially_impacted'

    return {
        'risk_level': risk_level,
        'findings': findings,
        'classification': classification
    }


def classify_view_impact(view_name: str, published: WorkbookStructure, candidate: WorkbookStructure,
                          diff: WorkbookDiff, workbook_classification: str) -> str:
    """Refine the workbook-level classification for one specific custom view, using its
    own base view (view_name) rather than applying 'directly_impacted' to every custom
    view whenever *any* view in the workbook was removed/renamed.
    """
    pub_views = published.worksheets | published.dashboards
    cand_views = candidate.worksheets | candidate.dashboards
    removed_views = pub_views - cand_views

    if view_name and view_name in removed_views:
        return 'directly_impacted'

    if workbook_classification == 'directly_impacted':
        # The view removal that drove the workbook-level rating belongs to a
        # different view - this one is only exposed to the remaining changes.
        return 'potentially_impacted' if diff.total > 0 else 'no_change'

    return workbook_classification


# Plain-English translation of each finding category, written for someone who
# doesn't know what a "data source filter" or a "Set" is but still needs to
# know whether a change is safe to publish. Keyed on the exact 'category'
# strings produced by classify_custom_view_impact() above.
_CATEGORY_PLAIN_LANGUAGE = {
    'Views': 'A sheet or dashboard was renamed or removed - any custom view built on it may stop working entirely.',
    'Fields': 'A field the workbook depends on was removed, renamed, or had its type changed - filters, sorts, or calculations built on it may break silently.',
    'Data Sources': "The workbook's data connection changed (a different data source, server, or database) - saved filters and parameters may no longer line up with the new data.",
    'Data Source Filters': 'A security filter built into the data source was added, removed, or changed - some users may now see more or less data than before, even though nothing on the sheet looks different.',
    'Parameters': "A parameter's name or allowed values changed - a custom view that saved a specific choice may show something different, or that choice may no longer be valid.",
    'Filters': 'A filter on a sheet changed - a custom view relying on the old filter setup may now show more, less, or different data.',
    'Marks': 'The way data is charted changed (for example, bars became a line) - saved formatting or selections in a custom view may no longer line up.',
    'Sorts': 'The default sort order changed - a custom view with its own saved sort may look different than it used to, or lose that custom order.',
    'Encodings': 'Which fields drive color, size, shape, or labels changed - a custom view may look visually different than what was originally saved.',
    'Calculations': "A calculated field's formula changed - any numbers or logic built on it may now come out differently.",
    'Groups & Sets': 'A saved grouping of values (a Group or Set) changed or was removed - filters and views built on that grouping may show different results or stop working.',
    'Actions': 'A dashboard action (for example, click-to-filter or a navigation link) changed or was removed - clicking on the dashboard may behave differently.',
    'Sheet Visibility': 'A worksheet tab was hidden - a dashboard or custom view expecting to show that tab may now be missing it.',
}

_SEVERITY_RANK = {'High': 0, 'Medium': 1, 'Low': 2}


def build_plain_language_summary(impact: dict, custom_views: list) -> dict:
    """Translates classify_custom_view_impact()'s findings, plus the per-view
    impact_severity already stamped onto each custom_views entry, into a
    short, jargon-free summary of whether - and why - this change affects
    existing custom views. Meant to be read by someone who never opens the
    Structural Diff or Impact Findings sections below it.

    Returns {'headline': str, 'tone': 'danger'|'warning'|'info'|'success',
    'reasons': [{'category', 'severity', 'text'}, ...] (highest severity
    first, one entry per category), 'view_counts': {...}, 'total_views': int}.
    """
    findings = impact.get('findings') or []

    best_by_category = {}
    for f in findings:
        cat = f.get('category')
        if cat is None:
            continue
        sev = f.get('severity', 'Low')
        current = best_by_category.get(cat)
        if current is None or _SEVERITY_RANK.get(sev, 2) < _SEVERITY_RANK.get(current['severity'], 2):
            best_by_category[cat] = f

    reasons = [
        {
            'category': cat,
            'severity': f.get('severity', 'Low'),
            'text': _CATEGORY_PLAIN_LANGUAGE.get(cat, f.get('detail') or f.get('title') or cat),
        }
        for cat, f in best_by_category.items()
    ]
    reasons.sort(key=lambda r: _SEVERITY_RANK.get(r['severity'], 2))

    total_views = len(custom_views or [])
    severity_counts = {'High': 0, 'Medium': 0, 'Low': 0, 'None': 0}
    for cv in (custom_views or []):
        sev = cv.get('impact_severity') or 'None'
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    directly = severity_counts['High']
    potentially = severity_counts['Medium']
    label_only = severity_counts['Low']
    unaffected = severity_counts['None']

    if total_views == 0:
        headline = 'This workbook has no existing custom views on record, so there is nothing this change could affect.'
        tone = 'info'
    elif directly:
        headline = f'Yes - {directly} of {total_views} existing custom view(s) will likely be affected by this change.'
        tone = 'danger'
    elif potentially:
        headline = (
            f"Possibly - {potentially} of {total_views} existing custom view(s) touch something that changed, "
            "though it isn't certain every one of them is affected."
        )
        tone = 'warning'
    elif label_only:
        headline = (
            f'Only a cosmetic change - {label_only} of {total_views} existing custom view(s) may show a '
            'different label, but should otherwise keep working.'
        )
        tone = 'info'
    else:
        headline = f'No - none of the {total_views} existing custom view(s) are expected to be affected by this change.'
        tone = 'success'

    return {
        'headline': headline,
        'tone': tone,
        'reasons': reasons,
        'view_counts': {
            'directly_impacted': directly,
            'potentially_impacted': potentially,
            'label_change_only': label_only,
            'no_change': unaffected,
        },
        'total_views': total_views,
    }
