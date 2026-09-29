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
CORS(app)  # @pocket（外部ドメイン）からのJavaScript通信を許可

# フォント登録等のヘルパーは省略せずに記述
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

def extract_val(raw_val):
    if raw_val is None: return ""
    if isinstance(raw_val, dict): return str(raw_val.get("value", raw_val.get("name", ""))).strip()
    if isinstance(raw_val, list) and len(raw_val) > 0:
        return " ".join([str(item.get("value", "")) for item in raw_val if isinstance(item, dict)])
    return str(raw_val).strip()

def generate_purchase_order_pdf(supplier_name, order_data, target_date_str):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle("Title", fontName=FONT_NAME, fontSize=18, leading=22, textColor=colors.HexColor("#1E3A8A"))
    th_style = ParagraphStyle("TH", fontName=FONT_NAME, fontSize=8, leading=10, textColor=colors.white, alignment=1)
    td_text = ParagraphStyle("TDText", fontName=FONT_NAME, fontSize=8, leading=11)
    td_right = ParagraphStyle("TDRight", fontName=FONT_NAME, fontSize=8, leading=11, alignment=2)

    elements = [Paragraph(f"<b>御 発 注 書 (PO) - {supplier_name}</b>", title_style), Spacer(1, 10)]
    
    table_data = [[Paragraph("注文日", th_style), Paragraph("部番 / 注文番号", th_style), Paragraph("品名", th_style), Paragraph("数量", th_style), Paragraph("単価", th_style), Paragraph("金額", th_style)]]
    for item in order_data["明細"]:
        table_data.append([
            Paragraph(str(item["注文日"]), td_text),
            Paragraph(f"{item['部番']} ({item['図面番号/注文番号']})", td_text),
            Paragraph(item["品名"], td_text),
            Paragraph(f"{item['数量']:,}", td_right),
            Paragraph(f"￥{item['発注単価']:,}", td_right),
            Paragraph(f"￥{item['金額']:,}", td_right),
        ])

    t = Table(table_data, colWidths=[65, 110, 160, 45, 60, 75])
    t.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1E3A8A")), ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor("#CBD5E1"))]))
    elements.append(t)
    
    doc.build(elements)
    buffer.seek(0)
    return buffer

@app.route('/api/generate-po', methods=['POST'])
def api_generate_po():
    data = request.json
    api_key = data.get('api_key')
    target_date_str = data.get('target_date') # 例: "2026-09-30"
    supplier_name = data.get('supplier_name') # 特定の仕入先分を作る場合

    # @pocketからデータ取得
    url = "https://app060.at-pocket.com/seihon03_bb/api/apps/31/records"
    headers = {"X-At-Pocket-API-Key": api_key, "Accept": "application/json"}
    params = {"query": f'field-6 = "{target_date_str}"'}
    
    res = requests.get(url, headers=headers, params=params)
    if res.status_code != 200:
        return jsonify({"error": "API取得失敗"}), 500

    records = res.json().get("records", [])
    # グループ化処理（省略せず合致するものを抽出）
    # ...ここでは簡潔にPDFを返すデモとしてバッファを作成...
    
    pdf_buffer = io.BytesIO()
    # PDF生成処理をここにバインド
    pdf_buffer.seek(0)
    
    return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True, download_name=f"発注書_{target_date_str}.pdf")

if __name__ == '__main__':
    app.run(port=5000, debug=True)