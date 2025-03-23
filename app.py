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
    """
    Instruct GPT to parse the user-provided `order_details` for:
    - Bill to address + phone
    - Item descriptions, prices, quantities
    - Subtotal, Tax, Shipping, Total
    """

    prompt = f"""
    You are a helpful system generating an invoice in JSON format.
    Parse the order details to identify:
    1. The customer's address and phone number (no names, just address + phone).
    2. A list of items purchased. For each item:
       - description (e.g., "Gray Sofa", "Queen Bed, White Color", etc.)
       - unit_price (numeric)
       - quantity (default 1 if not specified in the text)
       - amount = unit_price * quantity
    3. Subtotal = sum of all item amounts
    4. Tax (8.25%) = Subtotal * 0.0825 (round to 2 decimals)
    5. Shipping = 69.0 by default (unless the text states otherwise)
    6. Total = Subtotal + Tax + Shipping

    Return ONLY VALID JSON with EXACTLY this structure (no extra keys):

    {{
      "bill_to": "",
      "items": [
        {{
          "description": "",
          "unit_price": 0.0,
          "quantity": 1,
          "amount": 0.0
        }}
      ],
      "summary": {{
        "Subtotal": 0.0,
        "Tax (8.25%)": 0.0,
        "Shipping": 69.0,
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

    Make sure to fill out 'items' with as many items as are mentioned in the order details.
    If no shipping is specified, use 69.0 for shipping.
    """

    # Call OpenAI ChatCompletion
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )

    content = response.choices[0].message.content.strip()

    # Attempt to parse the response as JSON
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Attempt fallback: find the JSON substring if there's extra text
        match = re.search(r'(\{.*\})', content, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
        else:
            abort(400, "Failed to decode JSON from AI response.")

    # Build a final "delivery_summary" string from the JSON
    try:
        item_descriptions = []
        for item in data.get('items', []):
            desc = item.get('description', '')
            qty = item.get('quantity', 1)
            item_descriptions.append(f"{qty} x {desc}")

        combined_items = ", ".join(item_descriptions)
        contact_info = data['bill_to'].split()[-1]  # naive phone guess from last word
        total_str = data['summary']['Total']

        data['delivery_summary'] = (
            f"Delivery 🚚 {invoice_number}\n\n"
            f"{combined_items}\n\n"
            f"Address: {data['bill_to']}\n\n"
            f"Contact: {contact_info}\n\n"
            f"Total: ${float(total_str):.2f}"
        )
    except (KeyError, IndexError, TypeError):
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
    pdf.cell(80, 6, "Item Description", border=1)
    pdf.cell(30, 6, "Unit Price", border=1, align='R')
    pdf.cell(30, 6, "Quantity", border=1, align='R')
    pdf.cell(50, 6, "Amount", border=1, align='R', ln=True)

    pdf.set_font("Arial", '', 9)

    # Print each item row
    for item in data.get('items', []):
        description = item.get('description', '')
        unit_price = item.get('unit_price', 0.0)
        quantity = item.get('quantity', 1)
        amount = item.get('amount', 0.0)

        # Use multi_cell for description if it needs multiple lines
        line_height = 6
        top_y = pdf.get_y()
        x_pos = pdf.get_x()

        # Description
        pdf.multi_cell(80, line_height, description, border=1)
        # Remember bottom of the multi_cell
        bottom_y = pdf.get_y()

        # Move x to the next column on the same row’s top
        pdf.set_xy(x_pos + 80, top_y)

        height_of_cell = bottom_y - top_y
        # Unit price
        pdf.cell(30, height_of_cell, f"${float(unit_price):.2f}", border=1, align='R')
        # Quantity
        pdf.cell(30, height_of_cell, f"{quantity}", border=1, align='R')
        # Amount
        pdf.cell(50, height_of_cell, f"${float(amount):.2f}", border=1, align='R', ln=True)

    pdf.ln(5)

    # ---- Summary (Subtotal, Tax, Shipping, Total) ----
    summary = data.get('summary', {})
    # We'll specifically list them in a certain order
    pdf.cell(110, 6, "", border=0)
    pdf.cell(40, 6, "Subtotal", border=1, align='R')
    pdf.cell(40, 6, f"${summary.get('Subtotal', 0.0):.2f}", border=1, align='R', ln=True)

    pdf.cell(110, 6, "", border=0)
    pdf.cell(40, 6, "Tax (8.25%)", border=1, align='R')
    pdf.cell(40, 6, f"${summary.get('Tax (8.25%)', 0.0):.2f}", border=1, align='R', ln=True)

    pdf.cell(110, 6, "", border=0)
    pdf.cell(40, 6, "Shipping", border=1, align='R')
    pdf.cell(40, 6, f"${summary.get('Shipping', 69.0):.2f}", border=1, align='R', ln=True)

    pdf.cell(110, 6, "", border=0)
    pdf.cell(40, 6, "Total", border=1, align='R')
    pdf.cell(40, 6, f"${summary.get('Total', 0.0):.2f}", border=1, align='R', ln=True)

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
        # Grab the full text from the textarea
        orders_text = request.form.get("order_details", "").strip()

        # Split by ✅Name : to handle multiple orders in one paste, if that’s your format
        raw_orders = re.split(r'✅Name\s+:', orders_text)
        orders = [o.strip() for o in raw_orders if o.strip()]

        results = []
        for single_order_text in orders:
            invoice_number = get_next_invoice_number()
            data = generate_invoice(single_order_text, invoice_number)
            pdf_path = create_pdf(data, invoice_number)
            pdf_url = f"/invoices/{os.path.basename(pdf_path)}"

            results.append({
                "invoice_number": invoice_number,
                "pdf_url": pdf_url,
                "delivery_summary": data.get('delivery_summary', '')
            })

        return render_template("index.html", results=results)

    # If GET request, just show the form
    return render_template("index.html")

# -------------------------------------
# Route to serve PDF files
# -------------------------------------
@app.route("/invoices/<filename>")
def download_invoice(filename):
    invoices_dir = os.path.join(app.root_path, 'invoices')
    return send_from_directory(invoices_dir, filename, as_attachment=False)

# -------------------------------------
# Run the Flask app
# -------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
