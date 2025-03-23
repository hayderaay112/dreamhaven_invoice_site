import os
import json
import re
from flask import Flask, render_template, request, send_from_directory, abort
import openai
from fpdf import FPDF
from datetime import datetime

# Ensure invoices folder exists
if not os.path.exists("invoices"):
    os.makedirs("invoices")

# Set up environment variables for OpenAI
openai.organization = os.environ.get('ORGANIZATION')
openai.api_key = os.environ.get('API_KEY')

app = Flask(__name__)

# -------------------------------------
# Increase request size limit to allow
# large multiline text submissions
# -------------------------------------
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

# -------------------------------------
# Invoice number management, starting at 2500
# -------------------------------------
def get_next_invoice_number():
    invoice_file = "invoice_number.txt"
    if not os.path.exists(invoice_file):
        with open(invoice_file, "w") as f:
            f.write("2500")  # Start at 2500 if file does not exist

    with open(invoice_file, "r") as f:
        current_num = f.read().strip()

    try:
        invoice_number = int(current_num) + 1
    except ValueError:
        invoice_number = 2501  # fallback if the file is corrupted

    with open(invoice_file, "w") as f:
        f.write(str(invoice_number))

    return invoice_number

# -------------------------------------
# Generate structured invoice and delivery summary
# -------------------------------------
def generate_invoice(order_details, invoice_number):
    # This prompt guides the AI to return strictly valid JSON
    prompt = f"""
    Given the order details below, produce STRICTLY VALID JSON with the EXACT structure:

    {{
        "bill_to": "Customer address and contact number ONLY (no names)",
        "items": [
            {{
                "description": "Actual item details (including color)",
                "unit_price": 0.0,
                "amount": 0.0
            }},
            {{
                "description": "Optional additional item details (including color)",
                "unit_price": 0.0,
                "amount": 0.0
            }}
        ],
        "summary": {{
            "Subtotal": 0.0,
            "Tax (8.25%)": 0.0,
            "Shipping": 69.00,
            "Total": 0.0
        }},
        "terms": "All sales are final; no refunds. Special orders are not subject to cancellation. "
                 "A 30% restocking fee applies for seller-approved exchanges, cancellations, or returns. "
                 "Buyer assumes responsibility for transportation of merchandise picked up. "
                 "Seller is not liable for items that do not fit due to size constraints. "
                 "Delivery schedule changes require a 24-hour notice to avoid extra fees. "
                 "Report damages within three days for replacement of the damaged part.",
        "delivery_summary": ""
    }}

    Order Details:
    {order_details}
    """

    # Call OpenAI ChatCompletion
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )

    # Attempt to parse the response as JSON
    content = response.choices[0].message.content.strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Attempt naive fallback: find the JSON substring if there's extra text
        match = re.search(r'(\{.*\})', content, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
        else:
            abort(400, "Failed to decode JSON from AI response.")

    # Build delivery summary text
    try:
        item_descriptions = ', '.join(item['description'] for item in data['items'])
        # We'll parse phone from the last chunk of the bill_to line, if present
        contact_info = data['bill_to'].split()[-1]
        data['delivery_summary'] = (
            f"Delivery 🚚 {invoice_number}\n\n"
            f"{item_descriptions}\n\n"
            f"Address: {data['bill_to']}\n\n"
            f"Contact: {contact_info}\n\n"
            f"Total: ${data['summary']['Total']:.2f}"
        )
    except (KeyError, IndexError, TypeError):
        # If there's any structure issue, fill in something safe
        data['delivery_summary'] = "Error building delivery summary"

    return data

# -------------------------------------
# Create PDF with a clear layout
# -------------------------------------
def create_pdf(data, invoice_number):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # ---- Header Section ----
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(190, 8, "DREAMHAVEN BEDDING & FURNITURE", ln=True, align='C')

    pdf.set_font("Arial", '', 9)
    pdf.cell(190, 6, "www.dreamhavenbedding.com", ln=True, align='C')
    pdf.cell(190, 6, "Contacts: 682-424-2071 | 940-224-1232 | 469-825-2323", ln=True, align='C')

    pdf.ln(10)

    # ---- Invoice info ----
    date_str = datetime.now().strftime("%b %d, %Y")

    pdf.set_font("Arial", 'B', 10)
    pdf.cell(95, 6, f"Invoice #: {invoice_number}", border=0)
    pdf.cell(95, 6, f"Date: {date_str}", border=0, align='R', ln=True)
    pdf.cell(95, 6, "")
    pdf.cell(95, 6, f"Due Date: {date_str}", border=0, align='R', ln=True)

    pdf.ln(8)

    # ---- Bill To Section ----
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(190, 6, "Bill To:", ln=True)
    pdf.set_font("Arial", '', 9)
    pdf.multi_cell(190, 6, data.get('bill_to', ''))

    pdf.ln(5)

    # ---- Items Table Header ----
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(120, 6, "Item Description", border=1)
    pdf.cell(35, 6, "Unit Price", border=1, align='R')
    pdf.cell(35, 6, "Amount", border=1, align='R', ln=True)

    pdf.set_font("Arial", '', 9)

    # We will print each item in the table (multi-line if needed)
    for item in data.get('items', []):
        description = item.get('description', '')
        unit_price = item.get('unit_price', 0.0)
        amount = item.get('amount', 0.0)

        # Because we have to line-wrap descriptions, use multi_cell for it
        y_before = pdf.get_y()
        x_before = pdf.get_x()

        # First column (description)
        pdf.multi_cell(120, 6, description, border=1)
        y_after = pdf.get_y()

        # Move to the right side of that row
        pdf.set_xy(x_before + 120, y_before)

        cell_height = y_after - y_before

        # Unit price
        pdf.cell(35, cell_height, f"${unit_price:.2f}", border=1, align='R')
        # Amount
        pdf.cell(35, cell_height, f"${amount:.2f}", border=1, align='R', ln=True)

    pdf.ln(5)

    # ---- Summary (Subtotal, Tax, Shipping, Total) ----
    summary = data.get('summary', {})
    for key, value in summary.items():
        pdf.cell(120, 6, "", border=0)
        pdf.cell(35, 6, key, border=1, align='R')
        pdf.cell(35, 6, f"${value:.2f}", border=1, align='R', ln=True)

    pdf.ln(8)

    # ---- Terms and Conditions ----
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(0, 6, "Terms and Conditions:", ln=True)
    pdf.set_font("Arial", '', 8)
    pdf.multi_cell(0, 5, data.get('terms', ''))

    # Save PDF
    filename = f"invoices/Invoice_{invoice_number}.pdf"
    pdf.output(filename)
    return filename

# -------------------------------------
# Main route: index
# -------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # The entire text from the textarea
        orders_text = request.form.get("order_details", "").strip()

        # You can split them by a pattern if you want multiple orders
        # Example: '✅Name:' pattern
        # If you want to keep a simpler approach, you can skip splitting.
        # For now, let's keep your approach:
        raw_orders = re.split(r'✅Name\s+:', orders_text)
        orders = [o.strip() for o in raw_orders if o.strip()]

        results = []
        for _order in orders:
            invoice_number = get_next_invoice_number()
            data = generate_invoice(_order, invoice_number)
            pdf_path = create_pdf(data, invoice_number)
            pdf_url = f"/invoices/{os.path.basename(pdf_path)}"

            results.append({
                "invoice_number": invoice_number,
                "pdf_url": pdf_url,
                "delivery_summary": data.get('delivery_summary', '')
            })

        return render_template("index.html", results=results)

    # If GET request, just show form
    return render_template("index.html")

# -------------------------------------
# Route to serve PDF files
# -------------------------------------
@app.route("/invoices/<filename>")
def download_invoice(filename):
    # Safely send file from 'invoices' directory
    invoices_dir = os.path.join(app.root_path, 'invoices')
    return send_from_directory(invoices_dir, filename, as_attachment=False)

# -------------------------------------
# Run the Flask app
# -------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
