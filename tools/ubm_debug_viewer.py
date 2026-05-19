import argparse
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, dcc, html, Input, Output
import json

DEFAULT_KEY_TOPICS = {
    "run_parse": [
        "time",
        "local_pose_translation_x",
        "local_pose_translation_y",
        "local_pose_yaw",
        "traffic_light_status",
        "fusion_map_raw",
        "em_fusion_map_raw",
        "em_fusion_map",
        "fusion_obstacle3",
    ],
    "run_build_data": [
        "time",
        "local_pose_translation_x",
        "local_pose_translation_y",
        "local_pose_yaw",
    ],
    "run_feature_extract": [
        "obs_time",
        "o_object_id",
        "now_lane_id",
        "os_invade_rank",
        "os_has_invasion",
        "ego_to_stop_l",
    ],
}

MODULE_LABELS = {
    "run_parse": "_run_parse",
    "run_build_data": "_run_build_data",
    "run_feature_extract": "_run_feature_extract",
}


def _safe_read_pickle(path: Path) -> pd.DataFrame:
    return pd.read_pickle(path)


def _safe_read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def _find_files(root: Path, patterns: List[str]) -> List[Path]:
    files: List[Path] = []
    for pattern in patterns:
        files.extend(sorted(root.glob(pattern)))
    return files


@lru_cache(maxsize=8)
def _load_df(path_str: str) -> pd.DataFrame:
    path = Path(path_str)
    if path.suffixes[-2:] == [".pkl", ".zst"] or path.suffix == ".pkl":
        return _safe_read_pickle(path)
    return _safe_read_csv(path)


def _prepare_time_series(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    if time_col not in df.columns:
        return df
    if pd.api.types.is_datetime64_any_dtype(df[time_col]):
        return df
    df = df.copy()
    try:
        df[time_col] = pd.to_datetime(df[time_col], unit="ns", errors="coerce")
    except Exception:
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    return df


def _choose_time_col(df: pd.DataFrame) -> Optional[str]:
    for col in ("time", "obs_time"):
        if col in df.columns:
            return col
    return None


def _list_columns(df: pd.DataFrame) -> List[str]:
    return sorted([c for c in df.columns if c != "index"])


def _build_summary(df: pd.DataFrame, time_col: Optional[str]) -> Dict[str, str]:
    summary = {
        "rows": str(len(df)),
        "columns": str(len(df.columns)),
    }
    if time_col and time_col in df.columns:
        summary["time_min"] = str(df[time_col].min())
        summary["time_max"] = str(df[time_col].max())
    return summary


def _extract_paths(parse_pkl: str, parse_new: str, feat_dir: str) -> Dict[str, List[Path]]:
    paths: Dict[str, List[Path]] = {
        "run_parse": [],
        "run_build_data": [],
        "run_feature_extract": [],
    }

    if parse_pkl:
        paths["run_parse"].append(Path(parse_pkl))

    if parse_new:
        parse_new_path = Path(parse_new)
        if parse_new_path.is_dir():
            paths["run_build_data"] = _find_files(parse_new_path, ["*.pkl.zst", "*.pkl", "*.csv"])
        else:
            paths["run_build_data"].append(parse_new_path)

    if feat_dir:
        feat_path = Path(feat_dir)
        if feat_path.is_dir():
            paths["run_feature_extract"] = _find_files(feat_path, ["*_unified_obstacles.csv"])
        else:
            paths["run_feature_extract"].append(feat_path)

    return paths


def _latest_report(debug_dir: Path, pattern: str) -> Optional[Path]:
    candidates = list(debug_dir.rglob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _infer_paths_from_reports(debug_dir: Path) -> Dict[str, str]:
    paths = {"parse_pkl": "", "parse_new": "", "feat_dir": ""}
    parse_report = _latest_report(debug_dir, "*_parse_*.json")
    build_report = _latest_report(debug_dir, "*_build_data_*.json")
    feat_report = _latest_report(debug_dir, "*_feature_extract_*.json")

    for report_path, key in [
        (parse_report, "parse_pkl"),
        (build_report, "parse_new"),
        (feat_report, "feat_dir"),
    ]:
        if report_path is None:
            continue
        try:
            data = pd.read_json(report_path, typ="series")
        except Exception:
            continue
        if key == "parse_pkl":
            value = data.get("parse_pkl", "")
        elif key == "parse_new":
            value = data.get("parse_new_dir", "")
        else:
            value = data.get("feat_dir", "")
        if isinstance(value, str):
            paths[key] = value

    return paths


def _build_topic_options(columns: List[str]) -> List[Dict[str, str]]:
    return [{"label": c, "value": c} for c in columns]


def _build_summary_table(summary: Dict[str, str]) -> html.Table:
    rows = [html.Tr([html.Th(k), html.Td(v)]) for k, v in summary.items()]
    return html.Table(rows, style={"width": "100%", "border": "1px solid #ccc"})


def _plot_timeseries(df: pd.DataFrame, time_col: str, topic_col: str) -> go.Figure:
    fig = go.Figure()
    series = df[topic_col]
    x = df[time_col]

    if pd.api.types.is_numeric_dtype(series):
        fig.add_trace(go.Scatter(x=x, y=series, mode="lines", name=topic_col))
    else:
        fig.add_trace(go.Scatter(x=x, y=series.astype(str), mode="markers", name=topic_col))

    fig.update_layout(
        margin=dict(l=40, r=20, t=40, b=40),
        height=420,
        xaxis_title=time_col,
        yaxis_title=topic_col,
    )
    return fig


def _load_bin_report(debug_dir: Optional[Path]) -> Optional[Dict]:
    if debug_dir is None:
        return None
    report_path = _latest_report(debug_dir, "*_bin_parse_*.json")
    if report_path is None:
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_bin_cache_paths(bin_report: Optional[Dict], debug_dir: Optional[Path]) -> Dict[str, str]:
    cache_map = {}
    report_cache_dir = None
    report_bag_id = None
    if bin_report:
        report_cache_dir = bin_report.get("bin_cache_dir")
        report_bag_id = bin_report.get("bag_id")
        for item in bin_report.get("topics", []):
            topic = item.get("summary", {}).get("topic")
            key = item.get("key")
            cache_file = item.get("cache_file", "")
            if cache_file:
                if topic and topic not in cache_map:
                    cache_map[topic] = cache_file
                if key and key not in cache_map:
                    cache_map[key] = cache_file
            elif report_cache_dir and report_bag_id and key:
                candidate = Path(report_cache_dir) / f"{report_bag_id}_bin_view_cache_{key}.pkl"
                if candidate.exists():
                    cache_map[key] = str(candidate)
                    if topic and topic not in cache_map:
                        cache_map[topic] = str(candidate)
    search_root = Path(report_cache_dir) if report_cache_dir else debug_dir
    if search_root and search_root.exists():
        for cache_path in search_root.rglob("*_bin_view_cache_*.pkl"):
            parts = cache_path.name.split("_bin_view_cache_")
            if len(parts) == 2:
                key = parts[1].split(".pkl")[0]
                if key not in cache_map:
                    cache_map[key] = str(cache_path)
    return cache_map


@lru_cache(maxsize=4)
def _load_bin_cache(cache_path: str) -> List[Dict]:
    rows = pd.read_pickle(cache_path)
    if not isinstance(rows, list) or not rows:
        return []
    if not isinstance(rows[0], dict):
        return []
    return rows


def _extract_row_data(row: Dict) -> Dict:
    if not isinstance(row, dict):
        return {}
    data = row.get("data")
    if isinstance(data, dict):
        return data
    return row


def _infer_view_cache_path(bin_report: Dict, item: Dict) -> Optional[str]:
    cache_file = item.get("cache_file", "")
    if cache_file:
        return cache_file
    bag_id = bin_report.get("bag_id")
    cache_dir = bin_report.get("bin_cache_dir")
    key = item.get("key")
    if bag_id and cache_dir and key:
        candidate = Path(cache_dir) / f"{bag_id}_bin_view_cache_{key}.pkl"
        if candidate.exists():
            return str(candidate)
    return None


def _load_view_cache_structure(cache_path: str, max_depth: int = 4) -> List[str]:
    rows = _load_bin_cache(cache_path)
    if not rows:
        return []
    sample = _extract_row_data(rows[0])
    if not isinstance(sample, dict):
        return []
    return sorted(set(_flatten_keys(sample, max_depth=max_depth)))


def _flatten_keys(data: Dict, prefix: str = "", max_depth: int = 6) -> List[str]:
    keys = []
    if max_depth < 0:
        return keys
    if isinstance(data, dict):
        for k, v in data.items():
            path = f"{prefix}.{k}" if prefix else k
            keys.append(path)
            keys.extend(_flatten_keys(v, path, max_depth=max_depth - 1))
    return keys


def _get_value_by_path(data: Dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _build_bin_report_panel(bin_report: Optional[Dict]) -> html.Div:
    if not bin_report:
        return html.Div([html.H4("Bin Parse Report"), html.P("No bin report found.")])

    topics = bin_report.get("topics", [])
    rows = [
        html.Tr([
            html.Th("Topic"),
            html.Th("Count"),
            html.Th("Time Range"),
            html.Th("File Size"),
        ])
    ]
    for item in topics:
        summary = item.get("summary", {})
        time_range = summary.get("time_range")
        if time_range:
            time_str = f"{time_range.get('min')} ~ {time_range.get('max')}"
        else:
            time_str = "-"
        rows.append(
            html.Tr([
                html.Td(summary.get("topic", item.get("key", ""))),
                html.Td(str(summary.get("count", ""))),
                html.Td(time_str),
                html.Td(str(item.get("size", 0))),
            ])
        )

    structure_blocks = []
    for item in topics:
        summary = item.get("summary", {})
        fields = summary.get("structure", [])
        if not fields:
            cache_path = _infer_view_cache_path(bin_report, item)
            if cache_path:
                fields = [{"name": p} for p in _load_view_cache_structure(cache_path)]
        if fields:
            preview = ", ".join([f["name"] for f in fields[:12]])
            if len(fields) > 12:
                preview += " ..."
            structure_blocks.append(
                html.Li(f"{summary.get('topic', item.get('key', ''))}: {preview}")
            )

    return html.Div(
        [
            html.H4("Bin Parse Report"),
            html.Table(rows, style={"width": "100%", "border": "1px solid #ccc"}),
            html.H5("Topic Structure (preview)"),
            html.Ul(structure_blocks) if structure_blocks else html.P("No structure data."),
        ]
    )


def build_app(paths: Dict[str, List[Path]], bin_report: Optional[Dict], bin_cache_map: Dict[str, str]) -> Dash:
    app = Dash(__name__)

    default_module = "run_parse"
    module_paths = {k: [str(p) for p in v] for k, v in paths.items()}
    default_files = module_paths.get(default_module) or []
    default_file = default_files[0] if default_files else None

    app.layout = html.Div(
        style={"fontFamily": "Arial, sans-serif", "padding": "16px"},
        children=[
            html.H2("UBM Debug Viewer"),
            html.Div(
                style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"},
                children=[
                    html.Div(
                        children=[
                            html.Label("Module"),
                            dcc.Dropdown(
                                id="module-select",
                                options=[
                                    {"label": MODULE_LABELS[k], "value": k}
                                    for k in MODULE_LABELS
                                ],
                                value=default_module,
                                clearable=False,
                            ),
                        ]
                    ),
                    html.Div(
                        children=[
                            html.Label("Data File"),
                            dcc.Dropdown(
                                id="file-select",
                                options=[{"label": p, "value": p} for p in default_files],
                                value=default_file,
                                clearable=False,
                            ),
                        ]
                    ),
                ],
            ),
            html.Div(
                style={"marginTop": "12px", "display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"},
                children=[
                    html.Div(
                        children=[
                            html.Label("Topic"),
                            dcc.Dropdown(id="topic-select"),
                        ]
                    ),
                    html.Div(
                        children=[
                            html.Label("Object ID Filter (optional)"),
                            dcc.Dropdown(id="object-id-select"),
                        ]
                    ),
                ],
            ),
            html.Div(style={"marginTop": "12px"}, children=[dcc.Graph(id="topic-graph")]),
            html.Div(
                style={"marginTop": "12px", "display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"},
                children=[
                    html.Div(id="summary-panel"),
                    html.Div(id="key-topics-panel"),
                ],
            ),
            html.Div(style={"marginTop": "12px"}, children=[_build_bin_report_panel(bin_report)]),
            html.H3("Bin Topic Player"),
            html.Div(
                style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"},
                children=[
                    html.Div(
                        children=[
                            html.Label("Bin Topic"),
                            dcc.Dropdown(
                                id="bin-topic-select",
                                options=[{"label": k, "value": k} for k in bin_cache_map.keys()],
                            ),
                        ]
                    ),
                    html.Div(
                        children=[
                            html.Label("Field Path"),
                            dcc.Dropdown(id="bin-field-select"),
                        ]
                    ),
                ],
            ),
            html.Div(
                style={"marginTop": "8px"},
                children=[
                    html.Button("Play/Pause", id="bin-play-toggle", n_clicks=0),
                    dcc.Interval(id="bin-play-interval", interval=500, disabled=True),
                ],
            ),
            html.Div(style={"marginTop": "8px"}, children=[dcc.Slider(id="bin-time-slider")]),
            html.Div(id="bin-time-info", style={"marginTop": "6px"}),
            html.Div(style={"marginTop": "8px"}, children=[dcc.Graph(id="bin-field-graph")]),
            html.Div(style={"marginTop": "8px"}, children=[html.Pre(id="bin-json-view")]),
        ],
    )

    @app.callback(
        Output("file-select", "options"),
        Output("file-select", "value"),
        Input("module-select", "value"),
    )
    def update_files(module_key: str):
        options = [{"label": p, "value": p} for p in module_paths.get(module_key, [])]
        value = options[0]["value"] if options else None
        return options, value

    @app.callback(
        Output("topic-select", "options"),
        Output("topic-select", "value"),
        Output("object-id-select", "options"),
        Output("object-id-select", "value"),
        Output("summary-panel", "children"),
        Output("key-topics-panel", "children"),
        Input("file-select", "value"),
        Input("module-select", "value"),
    )
    def update_topics(file_path: Optional[str], module_key: str):
        if not file_path:
            return [], None, [], None, html.Div(), html.Div()
        df = _load_df(file_path)
        time_col = _choose_time_col(df)
        df = _prepare_time_series(df, time_col) if time_col else df

        columns = _list_columns(df)
        topic_options = _build_topic_options(columns)
        default_topic = None
        for col in DEFAULT_KEY_TOPICS.get(module_key, []):
            if col in columns:
                default_topic = col
                break
        if default_topic is None and columns:
            default_topic = columns[0]

        object_id_options = []
        object_id_value = None
        if "o_object_id" in df.columns:
            object_ids = sorted(df["o_object_id"].dropna().unique().tolist())
            object_id_options = [{"label": str(v), "value": v} for v in object_ids]
            object_id_value = object_ids[0] if object_ids else None

        summary = _build_summary(df, time_col)
        summary_table = _build_summary_table(summary)

        key_topics = DEFAULT_KEY_TOPICS.get(module_key, [])
        key_topics_panel = html.Div(
            [
                html.H4("Key Topics"),
                html.Ul([html.Li(t) for t in key_topics]),
            ]
        )

        return topic_options, default_topic, object_id_options, object_id_value, summary_table, key_topics_panel

    @app.callback(
        Output("topic-graph", "figure"),
        Input("file-select", "value"),
        Input("topic-select", "value"),
        Input("object-id-select", "value"),
    )
    def update_graph(file_path: Optional[str], topic: Optional[str], object_id: Optional[int]):
        if not file_path or not topic:
            return go.Figure()
        df = _load_df(file_path)
        time_col = _choose_time_col(df)
        if time_col is None:
            return go.Figure()
        df = _prepare_time_series(df, time_col)

        if object_id is not None and "o_object_id" in df.columns:
            df = df[df["o_object_id"] == object_id]

        if df.empty:
            return go.Figure()

        df = df[[time_col, topic]].dropna(subset=[time_col])
        return _plot_timeseries(df, time_col, topic)

    @app.callback(
        Output("bin-field-select", "options"),
        Output("bin-field-select", "value"),
        Output("bin-time-slider", "min"),
        Output("bin-time-slider", "max"),
        Output("bin-time-slider", "value"),
        Input("bin-topic-select", "value"),
    )
    def update_bin_fields(topic: Optional[str]):
        if not topic or topic not in bin_cache_map:
            return [], None, 0, 0, 0
        cache_path = bin_cache_map[topic]
        rows = _load_bin_cache(cache_path)
        if not rows:
            return [], None, 0, 0, 0
        sample = _extract_row_data(rows[0])
        if not isinstance(sample, dict):
            return [], None, 0, 0, 0
        try:
            depth = int(os.environ.get("UBM_BIN_VIEWER_DEPTH", "6"))
        except ValueError:
            depth = 6
        field_paths = sorted(set(_flatten_keys(sample, max_depth=depth)))
        options = [{"label": p, "value": p} for p in field_paths]
        default_field = options[0]["value"] if options else None
        return options, default_field, 0, len(rows) - 1, 0

    @app.callback(
        Output("bin-play-interval", "disabled"),
        Input("bin-play-toggle", "n_clicks"),
    )
    def toggle_play(n_clicks: int):
        if n_clicks is None:
            return True
        return n_clicks % 2 == 0

    @app.callback(
        Output("bin-time-slider", "value"),
        Input("bin-play-interval", "n_intervals"),
        Input("bin-time-slider", "value"),
        Input("bin-topic-select", "value"),
    )
    def advance_slider(_, current_value: Optional[int], topic: Optional[str]):
        if topic is None or topic not in bin_cache_map:
            return 0
        cache_path = bin_cache_map[topic]
        rows = _load_bin_cache(cache_path)
        if not rows:
            return 0
        if current_value is None:
            return 0
        return (current_value + 1) % len(rows)

    @app.callback(
        Output("bin-time-info", "children"),
        Output("bin-json-view", "children"),
        Output("bin-field-graph", "figure"),
        Input("bin-topic-select", "value"),
        Input("bin-time-slider", "value"),
        Input("bin-field-select", "value"),
    )
    def update_bin_view(topic: Optional[str], slider_value: Optional[int], field_path: Optional[str]):
        if not topic or topic not in bin_cache_map:
            return "", "", go.Figure()
        cache_path = bin_cache_map[topic]
        rows = _load_bin_cache(cache_path)
        if not rows:
            return "", "", go.Figure()
        idx = slider_value or 0
        idx = max(0, min(idx, len(rows) - 1))
        row = rows[idx]
        data = _extract_row_data(row)
        msg_ts = row.get("msg_timestamp")
        start_ts = rows[0].get("msg_timestamp")
        delta = (msg_ts - start_ts) if msg_ts is not None and start_ts is not None else None
        info = f"index={idx} msg_timestamp={msg_ts} delta_from_start={delta}"
        json_view = json.dumps(data if isinstance(data, dict) else {}, ensure_ascii=True, indent=2)

        fig = go.Figure()
        if field_path:
            xs = []
            ys = []
            for item in rows:
                xs.append(item.get("msg_timestamp"))
                value = _get_value_by_path(_extract_row_data(item), field_path)
                ys.append(value if isinstance(value, (int, float)) else None)
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=field_path))
            fig.update_layout(
                margin=dict(l=40, r=20, t=40, b=40),
                height=320,
                xaxis_title="msg_timestamp",
                yaxis_title=field_path,
            )
        return info, json_view, fig

    return app


def main():
    parser = argparse.ArgumentParser(description="UBM Debug Viewer")
    parser.add_argument("--parse_pkl", type=str, default=os.environ.get("UBM_PARSE_PKL", ""))
    parser.add_argument("--parse_new", type=str, default=os.environ.get("UBM_PARSE_NEW", ""))
    parser.add_argument("--feat_dir", type=str, default=os.environ.get("UBM_FEAT_DIR", ""))
    parser.add_argument("--debug_dir", type=str, default=os.environ.get("UBM_DEBUG_REPORT_DIR", ""))
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    args = parser.parse_args()

    bin_report = None
    bin_cache_map: Dict[str, str] = {}
    if args.debug_dir:
        debug_path = Path(args.debug_dir)
        inferred = _infer_paths_from_reports(debug_path)
        if not args.parse_pkl:
            args.parse_pkl = inferred.get("parse_pkl", "")
        if not args.parse_new:
            args.parse_new = inferred.get("parse_new", "")
        if not args.feat_dir:
            args.feat_dir = inferred.get("feat_dir", "")
        bin_report = _load_bin_report(debug_path)
        bin_cache_map = _load_bin_cache_paths(bin_report, debug_path)

    paths = _extract_paths(args.parse_pkl, args.parse_new, args.feat_dir)
    app = build_app(paths, bin_report, bin_cache_map)
    app.run(debug=False, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
