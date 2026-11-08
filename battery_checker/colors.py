"""配色（仕様 付録 B。日本の伝統色パレット）。Qt なしで読めるように theme.py から分けた。"""

COLORS: dict[str, str] = {
    # 全体
    "window_bg": "#F3EBE1",  # 鳥の子色の派生
    "panel_bg": "#FCF8F3",
    "panel_border": "#E2D3C2",
    "divider": "#E2D3C2",
    "input_bg": "#FFFCF8",
    "input_border": "#B0927A",  # 利休白茶
    "readonly_bg": "#EFE4D7",
    "readonly_border": "#E2D3C2",
    "text": "#27221F",  # 墨色
    "text_sub": "#656255",  # 利休鼠
    # ヘッダー
    "header_bg": "#2B2A28",
    "header_text": "#FBF8F1",
    "accent_band": "#9D2933",  # 臙脂色
    "badge_bg": "#B4581E",  # 朽葉色を濃くした色
    "badge_text": "#FBF8F1",
    # 数値・ボタン
    "current_badge_bg": "#A3282C",
    "current_badge_text": "#FFFFFF",
    "start_bg": "#D57835",  # 朽葉色
    "start_text": "#FBF8F1",
    "stop_bg": "#9D2933",
    "stop_text": "#FCF8F3",
    "save_bg": "#27221F",
    "save_text": "#FCF8F3",
    "range_sel_bg": "#B4581E",
    "range_sel_text": "#FCF8F3",
    # ステータスバー
    "status_bg": "#E2BE9F",
    "status_text": "#27221F",
    # グラフ
    "current_label": "#9A4F1C",
    "voltage_label": "#4E7A3A",  # 濃い緑
    "graph_bg": "#2B2A28",  # 描画領域は暗色（墨色に近い濃色）
    "graph_border": "#1E1C1A",
    "graph_grid": "#45403A",
    "graph_axis": "#656255",  # 軸の数値（描画領域の外、明るいパネル上）
    "current_line": "#D57835",
    "voltage_line": "#8CC27A",  # 緑（萌黄系）
    # ---- INA226_BatteryChecker で追加（付録 B） ----
    "voltage_badge_bg": "#A3282C",  # 電圧の数値バッジ（INA226_Monitor の電流バッジと同じ見た目）
    "voltage_badge_text": "#FFFFFF",
    "ok_bg": "#4E7A3A",  # OK の結果表示
    "ok_text": "#FFFFFF",
    "ng_bg": "#A3282C",  # NG の結果表示・NG のセル
    "ng_text": "#FFFFFF",
    "record_bg": "#D57835",  # 記録ボタン（INA226_Monitor の開始ボタンと同じ）
    "record_text": "#FBF8F1",
    "plot_ok": "#D57835",  # 分布グラフの点（OK）
    "plot_ng": "#E04848",  # 分布グラフの点（NG）
    "limit_line": "#E2BE9F",  # 上下限の線（破線）
    "xlsx_header_bg": "#2B2A28",  # Excel の見出し
    "xlsx_header_text": "#FBF8F1",
    "error_text": "#A3282C",  # ステータス欄の赤字
    "state_stable": "#4E7A3A",
    "state_unstable": "#B4581E",
    "state_none": "#656255",
}


# Excel 出力の色（openpyxl は # なしの RRGGBB）
XLSX_COLORS = {
    "header_bg": COLORS["xlsx_header_bg"].lstrip("#"),
    "header_text": COLORS["xlsx_header_text"].lstrip("#"),
    "ng_bg": COLORS["ng_bg"].lstrip("#"),
    "ng_text": COLORS["ng_text"].lstrip("#"),
    "bar": COLORS["plot_ok"].lstrip("#"),
}
