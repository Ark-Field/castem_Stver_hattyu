from datetime import date
import io
import os
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import pandas as pd
import requests

# ReportLab関連
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

app = Flask(__name__)
CORS(app)  # @pocketからのクロスドメイン通信を許可

# ==========================================================
# 1. 日本語フォント設定
# ==========================================================
def setup_japanese_font():
    local_font_path = "NotoSansJP-Regular.ttf"
    if os.path.exists(local_font_path):
        try:
            pdfmetrics.registerFont(TTFont("JPFont", local_font_path))
            return "JPFont"
        except Exception:
            pass
    return "Helvetica"

FONT_NAME = setup_japanese_font()

# ==========================================================
# 2. ヘルパー関数
# ==========================================================
def extract_val(raw_val):
    if raw_val is None: return ""
    if isinstance(raw_val, dict): return str(raw_val.get("value", raw_val.get("name", ""))).strip()
    if isinstance(raw_val, list) and len(raw_val) > 0:
        return " ".join([str(item.get("value", "")) for item in raw_val if isinstance(item, dict)])
    return str(raw_val).strip()

# ==========================================================
# 3. Canvas（総ページ数自動付与）
# ==========================================================
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont(FONT_NAME, 8.5)
        self.setFillColor(colors.HexColor("#1E293B"))
        self.drawCentredString(595.27 / 2.0, 20, f"{self._pageNumber} / {page_count}")
        self.restoreState()

# ==========================================================
# 4. 発注書PDF生成ロジック
# ==========================================================
def generate_purchase_order_pdf(supplier_name, order_data, target_date_str):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)

    title_style = ParagraphStyle("Title", fontName=FONT_NAME, fontSize=18, leading=22, textColor=colors.HexColor("#1E3A8A"))
    sub_style = ParagraphStyle("Sub", fontName=FONT_NAME, fontSize=9, leading=12, textColor=colors.HexColor("#3B82F6"), alignment=2)
    th_style = ParagraphStyle("TH", fontName=FONT_NAME, fontSize=8, leading=10, textColor=colors.white, alignment=1)
    td_text = ParagraphStyle("TDText", fontName=FONT_NAME, fontSize=8, leading=11, textColor=colors.HexColor("#1E293B"))
    td_right = ParagraphStyle("TDRight", fontName=FONT_NAME, fontSize=8, leading=11, textColor=colors.HexColor("#1E293B"), alignment=2)

    elements = [
        Paragraph("<b>御 発 注 書 (PO)</b>", title_style),
        Paragraph(f"注文日指定: {target_date_str}", sub_style),
        Spacer(1, 10),
        Paragraph(f"<b>{supplier_name} 御中</b>", ParagraphStyle("Supp", fontName=FONT_NAME, fontSize=12, leading=16)),
        Spacer(1, 10)
    ]

    table_data = [[
        Paragraph("注文日", th_style), Paragraph("部番 / 注文番号", th_style),
        Paragraph("品名 / 材質", th_style), Paragraph("数量", th_style),
        Paragraph("発注単価", th_style), Paragraph("金額（税別）", th_style),
    ]]

    for item in order_data["明細"]:
        dwg_ord = []
        if item["部番"]: dwg_ord.append(item["部番"])
        if item["図面番号/注文番号"]: dwg_ord.append(f"({item['図面番号/注文番号']})")
        name_mat = item["品名"]
        if item["材質"]: name_mat += f" [{item['材質']}]"

        table_data.append([
            Paragraph(str(item["注文日"]), td_text),
            Paragraph(" ".join(dwg_ord), td_text),
            Paragraph(name_mat, td_text),
            Paragraph(f"{item['数量']:,}", td_right),
            Paragraph(f"￥{item['発注単価']:,}", td_right),
            Paragraph(f"￥{item['金額']:,}", td_right),
        ])

    details_table = Table(table_data, colWidths=[65, 110, 160, 45, 60, 75], repeatRows=1)
    details_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
    ]))
    elements.append(details_table)

    doc.build(elements, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer

# ==========================================================
# 5. APIエンドポイント（@pocketのJSから呼ばれる）
# ==========================================================
@app.route('/api/generate-po', methods=['POST'])
def api_generate_po():
    data = request.json or {}
    target_date_str = data.get('target_date') # 例: "2026/09/07"
    
    if not target_date_str:
        return jsonify({"error": "対象の注文日が指定されていません。"}), 400

    # Secrets（または環境変数）からAPIキーを安全に取得
    api_key = os.environ.get("app31_api_key", os.environ.get("pockets_api_key", ""))
    if not api_key:
        return jsonify({"error": "サーバー側のAPIキーが設定されていません。"}), 500

    # @pocket APIからデータ取得 (アプリID: 31)
    # 注文日フィールドを field-6 から field-7 に修正
    url = "https://app060.at-pocket.com/seihon03_bb/api/apps/31/records"
    headers = {"X-At-Pocket-API-Key": api_key, "Accept": "application/json"}
    params = {"query": f'field-7 = "{target_date_str}"'}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=15)
        if res.status_code != 200:
            return jsonify({"error": f"@pocket APIエラー: {res.status_code}"}), 500

        records = res.json().get("records", res.json().get("data", []))

        # 発注先名別（field-3）に集計・グループ化
        parsed_list = []
        for r in records:
            inner = r.get("record", r)
            supplier = extract_val(inner.get("field-3", ""))
            if not supplier: supplier = "（発注先未設定）"

            def safe_float(val):
                try: return float(str(val).replace(",", ""))
                except: return 0.0

            qty = safe_float(inner.get("field-13", 0))
            cost_price = safe_float(inner.get("field-19", 0))
            amount = qty * cost_price

            parsed_list.append({
                "発注先名": supplier, "注文日": target_date_str,
                "部番": extract_val(inner.get("field-9", "")),
                "品名": extract_val(inner.get("field-10", "")),
                "材質": extract_val(inner.get("field-12", "")),
                "図面番号/注文番号": extract_val(inner.get("field-11", "")),
                "数量": qty, "発注単価": cost_price, "金額": amount,
            })

        if not parsed_list:
            return jsonify({"error": "指定された注文日に該当するデータがありません。"}), 404

        df = pd.DataFrame(parsed_list)
        
        # 最初の発注先のPDFを生成して返す
        first_supplier = df["発注先名"].iloc[0]
        supplier_group = df[df["発注先名"] == first_supplier]
        
        order_data = {
            "明細": supplier_group.to_dict(orient="records"),
            "発注金額合計": int(supplier_group["金額"].sum()),
            "件数": len(supplier_group)
        }

        pdf_buffer = generate_purchase_order_pdf(first_supplier, order_data, target_date_str)
        
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"発注書_{first_supplier}_{target_date_str.replace('/', '')}.pdf"
        )

    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        print("=== サーバー側エラー詳細 ===")
        print(err_detail)
        # ★エラーの発生源と内容をそのまま画面に返すことで、原因が一発で分かります
        return jsonify({"error": f"サーバー内部エラー: {str(e)} | 詳細: {err_detail}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)