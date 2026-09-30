from datetime import date
import io
import os
import traceback
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import pandas as pd
import requests

# ReportLab関連
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
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
# 3. Canvas（総ページ数自動付与・A4横対応）
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
        self.setFont(FONT_NAME, 8)
        self.setFillColor(colors.HexColor("#1E293B"))
        self.drawCentredString(841.89 / 2.0, 20, f"{self._pageNumber} / {page_count}")
        self.restoreState()

# ==========================================================
# 4. A4横 発注書PDF生成ロジック
# ==========================================================
def generate_purchase_order_pdf(supplier_name, order_data, target_date_str):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=30, rightMargin=30, topMargin=30, bottomMargin=30)

    # スタイル定義
    style_title = ParagraphStyle("Title", fontName=FONT_NAME, fontSize=16, leading=20, alignment=1, textColor=colors.HexColor("#000000"))
    style_meta = ParagraphStyle("Meta", fontName=FONT_NAME, fontSize=8, leading=11, alignment=2, textColor=colors.HexColor("#000000"))
    style_supplier = ParagraphStyle("Supp", fontName=FONT_NAME, fontSize=11, leading=15, textColor=colors.HexColor("#000000"))
    style_company = ParagraphStyle("Comp", fontName=FONT_NAME, fontSize=8.5, leading=12, textColor=colors.HexColor("#000000"))
    style_company_right = ParagraphStyle("CompR", fontName=FONT_NAME, fontSize=8, leading=11, alignment=0, textColor=colors.HexColor("#000000"))
    style_th = ParagraphStyle("TH", fontName=FONT_NAME, fontSize=8, leading=10, textColor=colors.black, alignment=1)
    style_td = ParagraphStyle("TD", fontName=FONT_NAME, fontSize=8, leading=11, textColor=colors.black)
    style_td_right = ParagraphStyle("TDR", fontName=FONT_NAME, fontSize=8, leading=11, alignment=2, textColor=colors.black)

    elements = []

    def h_line(height=1, color=colors.black):
        t = Table([['']], colWidths=[781], rowHeights=[height])
        t.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), color)]))
        return t

    # 1. タイトル行 ＆ 管理番号・発注日
    elements.append(Paragraph("<b>注文書</b>", style_title))
    elements.append(Spacer(1, 2))
    elements.append(h_line(2, colors.HexColor("#666666")))
    elements.append(Spacer(1, 5))

    meta_text = f"管理番号 3016<br/>発注日 {target_date_str}"
    elements.append(Paragraph(meta_text, style_meta))
    elements.append(Spacer(1, 10))

    # 2. 宛先 ＆ 自社情報セクション
    supp_para = Paragraph(f"<b>{supplier_name}</b> 様", style_supplier)
    
    company_info_html = (
        "<b>株式会社 キャステム</b><br/>"
        "〒275-0016 千葉県習志野市津田沼7-18-25<br/>"
        "TEL 047-452-9541 FAX 047-451-5843<br/>"
        "MAIL info@castem.info"
    )
    comp_para = Paragraph(company_info_html, style_company_right)

    header_table = Table([[supp_para, comp_para]], colWidths=[380, 401])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    elements.append(header_table)
    elements.append(h_line(1.5, colors.HexColor("#000000")))
    elements.append(Spacer(1, 8))

    # 3. 条件欄（納期・納品先・住所など） ＆ ご挨拶文
    # グループ内の最初（あるいは代表）の明細から納品先・住所を取得
    first_item = order_data["明細"][0] if order_data["明細"] else {}
    delivery_place = first_item.get("納品先", "")
    delivery_address = first_item.get("納品先住所", "")

    conditions_html = (
        "納期： 記載の通り ／ 運賃： 含む<br/>"
        f"納品先： {delivery_place}<br/>"
        f"住所： {delivery_address}<br/>"
        "受渡場所： 打合せ ／ お支払条件： 従来通り"
    )
    cond_para = Paragraph(conditions_html, style_company)
    msg_para = Paragraph("※下記の通りご注文申し上げます。", style_company)

    cond_table = Table([[cond_para, msg_para]], colWidths=[380, 401])
    cond_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    elements.append(cond_table)
    elements.append(Spacer(1, 8))

    # 4. 詳細明細表（8カラム構成）
    table_data = [[
        Paragraph("区分", style_th),
        Paragraph("図番", style_th),
        Paragraph("品名", style_th),
        Paragraph("注文番号", style_th),
        Paragraph("材質", style_th),
        Paragraph("数量", style_th),
        Paragraph("単位", style_th),
        Paragraph("希望納期", style_th),
    ]]

    for item in order_data["明細"]:
        table_data.append([
            Paragraph(str(item.get("区分", "済み")), style_td),
            Paragraph(str(item.get("図番", "")), style_td),
            Paragraph(str(item.get("品名", "")), style_td),
            Paragraph(str(item.get("注文番号", "")), style_td),
            Paragraph(str(item.get("材質", "")), style_td),
            Paragraph(f"{item['数量']:,}", style_td_right),
            Paragraph(str(item.get("単位", "個")), style_td),
            Paragraph(str(item.get("希望納期", target_date_str)), style_td),
        ])

    while len(table_data) < 12:
        table_data.append([Paragraph("", style_td)] * 8)

    details_table = Table(table_data, colWidths=[60, 100, 200, 100, 100, 60, 41, 120], repeatRows=1)
    details_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#64748B")),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(details_table)
    elements.append(Spacer(1, 10))

    # 5. 備考欄
    memo_header = Paragraph("<b>備考</b>", style_td)
    memo_content = Paragraph("現型支給", style_td)
    
    memo_table = Table([[memo_header], [memo_content]], colWidths=[781], rowHeights=[15, 30])
    memo_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#64748B")),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(memo_table)

    doc.build(elements, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer

# ==========================================================
# 5. APIエンドポイント
# ==========================================================
@app.route('/api/generate-po', methods=['POST'])
def api_generate_po():
    data = request.json or {}
    target_date_str = data.get('target_date')
    
    if not target_date_str:
        return jsonify({"error": "対象の注文日が指定されていません。"}), 400

    api_key = os.environ.get("app31_api_key", os.environ.get("pockets_api_key", ""))
    if not api_key:
        return jsonify({"error": "サーバー側のAPIキーが設定されていません。"}), 500

    formatted_date_str = target_date_str.replace('/', '-')

    url = "https://app060.at-pocket.com/seihon03_bb/api/apps/31/records"
    headers = {"X-At-Pocket-API-Key": api_key, "Accept": "application/json"}
    params = {"query": f'field-7 = "{formatted_date_str}"'}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=15)
        
        if res.status_code != 200:
            err_msg = f"@pocket APIエラー ({res.status_code}): {res.text}"
            print(err_msg)
            return jsonify({"error": err_msg}), 500

        records = res.json().get("records", res.json().get("data", []))

        parsed_list = []
        for r in records:
            inner = r.get("record", r)
            supplier = extract_val(inner.get("field-3", ""))
            if not supplier: supplier = "（発注先未設定）"

            def safe_float(val):
                try: return float(str(val).replace(",", ""))
                except: return 0.0

            qty = safe_float(inner.get("field-13", 0))

            parsed_list.append({
                "発注先名": supplier, 
                "注文日": target_date_str,
                "区分": extract_val(inner.get("field-8", "済み")),
                "図番": extract_val(inner.get("field-9", "")),
                "品名": extract_val(inner.get("field-10", "")),
                "注文番号": extract_val(inner.get("field-11", "")),
                "材質": extract_val(inner.get("field-12", "")),
                "数量": qty,
                "単位": "個",
                "希望納期": target_date_str,
                # ★ ご指定の納品先（field-28）と住所（field-29）を追加
                "納品先": extract_val(inner.get("field-28", "")),
                "納品先住所": extract_val(inner.get("field-29", "")),
            })

        if not parsed_list:
            return jsonify({"error": f"指定された注文日 ({target_date_str}) に該当するデータがありません。"}), 404

        df = pd.DataFrame(parsed_list)
        
        first_supplier = df["発注先名"].iloc[0]
        supplier_group = df[df["発注先名"] == first_supplier]
        
        order_data = {
            "明細": supplier_group.to_dict(orient="records"),
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
        err_detail = traceback.format_exc()
        print(err_detail)
        return jsonify({"error": f"サーバー内部エラー: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)