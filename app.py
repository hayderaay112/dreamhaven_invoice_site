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

openai.organization = os.environ.get('ORGANIZATION') 
openai.api_key = os.environ.get('API_KEY')

app = Flask(__name__)

# OPTIONAL: Increase max content size to 16 MB (or remove if not needed).
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

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

def clean_json_response(raw_response):
    """
    Removes or replaces common problematic characters (zero-width spaces,
    smart quotes, code fences) so the string is valid JSON.
    """
    cleaned = raw_response.strip()
    # Remove zero-width spaces
    cleaned = re.sub(r'[\u200B-\u200D\uFEFF]', '', cleaned)
    # Replace fancy quotes with standard quotes
    cleaned = cleaned.replace('“', '"').replace('”', '"')
    cleaned = cleaned.replace('‘', "'").replace('’', "'")

    # Remove code fences if present
    if "```" in cleaned:
        parts = cleaned.split("```json")
        if len(parts) > 1:
            chunk = parts[-1]
            chunk = chunk.split("```")[0]
            cleaned = chunk.strip()
    
    return cleaned

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
                "unit_price": 0.0,
                "amount": 0.0
            }},
            {{
                "description": "Additional actual item details, clearly stated including color",
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
    """

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )

    raw_content = response.choices[0].message.content
    cleaned_response = clean_json_response(raw_content)

    # Attempt JSON parsing with fallback
    try:
        data = json.loads(cleaned_response)
    except json.JSONDecodeError as e:
        print("ERROR: JSON decoding failed.")
        print("Raw OpenAI response:", raw_content)
        print("Cleaned response:", cleaned_response)
        raise ValueError(f"Invalid JSON from OpenAI: {e}")

    # Build the dynamic delivery summary
    item_descriptions = ', '.join(item['description'] for item in data['items'])
    data['delivery_summary'] = (
        f"Delivery 🚚 {invoice_number}\n\n"
        f"{item_descriptions}\n\n"
        f"Address: {data['bill_to']}\n\n"
        f"Contact: {data['bill_to'].split()[-1]}\n\n"
        f"Total: ${data['summary']['Total']:.2f}"
    )
    return data

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

    # Collect descriptions
    descriptions = "\n".join(item['description'] for item in data['items'])
    pdf.multi_cell(120, 6, descriptions, border=1)
    cell_height = pdf.get_y() - start_y

    # Move back up to fill the other columns
    pdf.set_xy(130, start_y)
    pdf.cell(35, cell_height, f"${data['summary']['Subtotal']:.2f}", border=1, align='R')
    pdf.cell(35, cell_height, f"${data['summary']['Subtotal']:.2f}", border=1, align='R', ln=True)

    pdf.ln(5)

    # Summaries
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

        # 1) Clean the user input of zero-width spaces
        orders_text = re.sub(r'[\u200B-\u200D\uFEFF]', '', orders_text)

        # 2) Use a regex to find blocks of text from "Name:" to "Phone:" to next "Name:" or end
        #    This pattern:
        #    - Looks for "Name:\s*(...)\s*Phone:\s*(...)\s*"
        #    - Then captures everything until the next "Name:" or the end.
        pattern = re.compile(
            r"Name:\s*(?P<name>.*?)\s*"
            r"Phone:\s*(?P<phone>.*?)\s*"
            r"(?P<details>(?:(?!Name:).)*)",  # anything until next "Name:" or EOF
            re.DOTALL
        )

        matches = pattern.finditer(orders_text)
        results = []

        for match in matches:
            # Extract the fields from each chunk
            name_str = match.group("name").strip()
            phone_str = match.group("phone").strip()
            details_str = match.group("details").strip()

            # Combine them into a single string for the AI prompt
            # (You can adapt the format as needed.)
            combined_order_text = (
                f"Name: {name_str}\n"
                f"Phone: {phone_str}\n"
                f"{details_str}"
            )

            invoice_number = get_next_invoice_number()
            data = generate_invoice(combined_order_text, invoice_number)
            pdf_path = create_pdf(data, invoice_number)
            pdf_url = f"/invoices/{os.path.basename(pdf_path)}"

            results.append({
                "invoice_number": invoice_number,
                "pdf_url": pdf_url,
                "delivery_summary": data['delivery_summary']
            })

        return render_template("index.html", results=results)

    return render_template("index.html")

@app.route('/invoices/<filename>')
def download_invoice(filename):
    return send_from_directory(os.path.join(app.root_path, 'invoices'), filename)

if __name__ == "__main__":
    app.run(debug=True)
