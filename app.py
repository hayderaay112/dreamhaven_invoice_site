import os
import json
import re
from flask import Flask, render_template, request, send_from_directory
import openai
from fpdf import FPDF
from datetime import datetime

# Ensure invoices folder exists
if not os.path.exists("invoices"):
    os.makedirs("invoices")

# Corrected API key setup (securely fetching from environment)
openai.api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)

# Invoice number management starting from 2500
def get_next_invoice_number():
    invoice_file = "invoice_number.txt"
    if not os.path.exists(invoice_file):
        with open(invoice_file, "w") as f:
            f.write("2500")  # Start at 2500 if file does not exist

    with open(invoice_file, "r") as f:
        invoice_number = int(f.read().strip()) + 1

    with open(invoice_file, "w") as f:
        f.write(str(invoice_number))

    return invoice_number

# Generate structured invoice and delivery summary
def generate_invoice(order_details, invoice_number):
    prompt = f"""
    Given order details:
    {order_details}

    Return ONLY VALID JSON matching exactly this structure:

    {{
        "bill_to": "Customer address and contact number ONLY (no names)",
        "items": [
            {{
                "description": "Actual item details clearly stated, including color",
                "unit_price": price,
                "amount": price
            }},
            {{
                "description": "Additional actual item details, clearly stated including color",
                "unit_price": 0.0,
                "amount": 0.0
            }}
        ],
        "summary": {{
            "Subtotal": amount,
            "Tax (8.25%)": amount,
            "Shipping": 69.00,
            "Total": amount
        }},
        "terms": "All sales are final; no refunds. Special orders are not subject to cancellation. "
                 "A 30% restocking fee applies for seller-approved exchanges, cancellations, or returns. "
                 "Buyer assumes responsibility for transportation of merchandise picked up. "
                 "Seller is not liable for items that do not fit due to size constraints. "
                 "Delivery schedule changes require a 24-hour notice to avoid extra fees. "
                 "Report damages within three days for replacement of the damaged part.",
        "delivery_summary": ""
    }}
    """

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )

    try:
        data = json.loads(response.choices[0].message.content.strip())
        item_descriptions = ', '.join(item['description'] for item in data['items'])
        data['delivery_summary'] = (
            f"Delivery 🚚 {invoice_number}\n\n"
            f"{item_descriptions}\n\n"
            f"Address: {data['bill_to']}\n\n"
            f"Contact: {data['bill_to'].split()[-1]}\n\n"
            f"Total: ${data['summary']['Total']:.2f}"
        )
        return data
    except json.JSONDecodeError:
        cleaned_response = response.choices[0].message.content.strip().split("```json")[-1].split("```")[0]
        return json.loads(cleaned_response)

# Create PDF with clearly structured layout
def create_pdf(data, invoice_number):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Arial", 'B', 12)
    pdf.cell(190, 8, "DREAMHAVEN BEDDING & FURNITURE", ln=True, align='C')
    pdf.set_font("Arial", '', 9)
    pdf.cell(190, 6, "www.dreamhavenbedding.com", ln=True, align='C')
    pdf.cell(190, 6, "Contacts: 682-424-2071 | 940-224-1232 | 469-825-2323", ln=True, align='C')

    pdf.ln(10)

    date_str = datetime.now().strftime("%b %d, %Y")
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(95, 6, f"Invoice #: {invoice_number}", border=0)
    pdf.cell(95, 6, f"Date: {date_str}", border=0, align='R', ln=True)
    pdf.cell(95, 6, "")
    pdf.cell(95, 6, f"Due Date: {date_str}", border=0, align='R', ln=True)

    pdf.ln(8)

    pdf.set_font("Arial", 'B', 9)
    pdf.cell(190, 6, "Bill To:", ln=True)
    pdf.set_font("Arial", '', 9)
    pdf.multi_cell(190, 6, data['bill_to'])

    pdf.ln(5)

    pdf.set_font("Arial", 'B', 9)
    pdf.cell(120, 6, "Item Description", border=1)
    pdf.cell(35, 6, "Unit Price", border=1, align='R')
    pdf.cell(35, 6, "Amount", border=1, align='R', ln=True)

    pdf.set_font("Arial", size=9)
    start_y = pdf.get_y()
    descriptions = "\n".join(item['description'] for item in data['items'])
    pdf.multi_cell(120, 6, descriptions, border=1)
    cell_height = pdf.get_y() - start_y
    pdf.set_xy(130, start_y)
    pdf.cell(35, cell_height, f"${data['summary']['Subtotal']:.2f}", border=1, align='R')
    pdf.cell(35, cell_height, f"${data['summary']['Subtotal']:.2f}", border=1, align='R', ln=True)

    pdf.ln(5)

    for key, value in data['summary'].items():
        pdf.cell(120, 6, "", border=0)
        pdf.cell(35, 6, key, border=1, align='R')
        pdf.cell(35, 6, f"${value:.2f}", border=1, align='R', ln=True)

    pdf.ln(8)

    pdf.set_font("Arial", 'B', 9)
    pdf.cell(0, 6, "Terms and Conditions:", ln=True)
    pdf.set_font("Arial", '', 8)
    pdf.multi_cell(0, 5, data['terms'])

    filename = f"invoices/Invoice_{invoice_number}.pdf"
    pdf.output(filename)
    return filename

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        orders_text = request.form["order_details"]
        orders = [order.strip() for order in re.split(r'✅Name\s+:', orders_text) if order.strip()]

        results = []
        for order in orders:
            invoice_number = get_next_invoice_number()
            data = generate_invoice(order, invoice_number)
            pdf_path = create_pdf(data, invoice_number)
            pdf_url = f"/invoices/{os.path.basename(pdf_path)}"

            results.append({
                "invoice_number": invoice_number,
                "pdf_url": pdf_url,
                "delivery_summary": data['delivery_summary']
            })

        return render_template("index.html", results=results)

    return render_template("index.html")

# Fixed PDF download route clearly for Render:
@app.route('/invoices/<filename>')
def download_invoice(filename):
    return send_from_directory(os.path.join(app.root_path, 'invoices'), filename)

if __name__ == "__main__":
    app.run(debug=True)
