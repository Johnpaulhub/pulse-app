import os
import io
from flask import Flask, render_template, request, send_file
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# Initialize the Flask application
app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Retrieve form data safely from the HTML form
        # (No walrus operators used here to prevent SyntaxErrors)
        client_name = request.form.get('client_name', 'Valued Customer')
        amount = request.form.get('amount', '0.00')
        description = request.form.get('description', 'Payment for services')
        
        # --- PDF Generation ---
        # Create a buffer to hold the PDF data in memory
        buffer = io.BytesIO()
        
        # Generate the PDF using ReportLab
        p = canvas.Canvas(buffer, pagesize=letter)
        
        # -- Draw receipt content --
        # Title
        p.setFont("Helvetica-Bold", 20)
        p.drawString(100, 750, "OFFICIAL RECEIPT")
        
        # Details
        p.setFont("Helvetica", 12)
        p.drawString(100, 700, f"Client Name: {client_name}")
        p.drawString(100, 670, f"Amount: KES {amount}")
        p.drawString(100, 640, f"Description: {description}")
        
        # Footer
        p.setFont("Helvetica-Oblique", 10)
        p.drawString(100, 600, "Thank you for your payment.")
        
        # Finalize PDF
        p.showPage()
        p.save()
        
        # Move the buffer's cursor to the beginning
        buffer.seek(0)
        
        # Send the generated PDF as a downloadable attachment
        return send_file(
            buffer, 
            as_attachment=True, 
            download_name=f"receipt_{client_name.replace(' ', '_')}.pdf", 
            mimetype='application/pdf'
        )
        
    # --- Render HTML Form ---
    # This part runs when you visit the page with a GET request
    # We are including the full styling here to ensure it looks correct on deployment.
    return '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Pulse Receipt Generator</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background-color: #0b0e14;
                color: #f3f4f6;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }
            .container {
                max-width: 400px;
                width: 90%;
                background-color: #151a21;
                padding: 24px;
                border-radius: 14px;
                border: 1px solid #2d3748;
                box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            }
            h2 {
                text-align: center;
                color: #3b82f6;
                margin-top: 0;
                margin-bottom: 20px;
            }
            label {
                display: block;
                margin-bottom: 6px;
                font-size: 0.85rem;
                color: #9ca3af;
                font-weight: 500;
            }
            input, textarea {
                width: 100%;
                padding: 12px;
                margin-bottom: 16px;
                box-sizing: border-box;
                border-radius: 10px;
                border: 1px solid #2d3748;
                background-color: #090d16;
                color: #f3f4f6;
                font-size: 1rem;
                outline: none;
                font-family: inherit;
            }
            input:focus, textarea:focus {
                border-color: #3b82f6;
            }
            textarea {
                resize: vertical;
                height: 80px;
            }
            button {
                width: 100%;
                padding: 12px;
                border: none;
                border-radius: 20px;
                font-weight: 600;
                cursor: pointer;
                font-size: 0.95rem;
                background-color: #3b82f6;
                color: white;
                transition: background-color 0.2s;
                margin-top: 10px;
            }
            button:hover {
                background-color: #2563eb;
            }
            .footer-text {
                text-align: center;
                color: #6b7280;
                font-size: 0.75rem;
                margin-top: 15px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Generate Receipt</h2>
            <form method="POST">
                <label for="client_name">Client Name:</label>
                <input type="text" id="client_name" name="client_name" placeholder="e.g. John Doe" required>
                
                <label for="amount">Amount (KES):</label>
                <input type="number" id="amount" name="amount" placeholder="e.g. 1000" required>
                
                <label for="description">Description:</label>
                <textarea id="description" name="description" placeholder="e.g. Monthly subscription" required></textarea>
                
                <button type="submit">Generate PDF</button>
            </form>
            <div class="footer-text">Powered by Pulse</div>
        </div>
    </body>
    </html>
    '''

# Run the app if executed directly
if __name__ == '__main__':
    # Note: In production on Render, Gunicorn is used instead of app.run()
    app.run(debug=True, port=5000)
