import sqlite3
import os
from fpdf import FPDF

# Ensure the uploads folder exists
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Connect to your database
conn = sqlite3.connect('Instances/MRP_database.db')
cursor = conn.cursor()

# Fetch all documents that have a FileName (or all documents)
cursor.execute("""
    SELECT DocumentID, DocumentNumber, Title, Revision, ProjectID, DocumentType, Status 
    FROM t_EngineeringDocument 
    WHERE FileName IS NOT NULL
""")
docs = cursor.fetchall()

for doc_id, doc_num, title, rev, proj_id, doc_type, status in docs:
    # Construct filename (as stored in DB – adjust if needed)
    filename = f"{doc_num}_Rev{rev}.pdf"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 24)
    pdf.cell(0, 20, txt=f"Document: {doc_num}", ln=True, align='C')
    pdf.set_font("Arial", size=20)
    pdf.cell(0, 15, txt=f"Revision: {rev}", ln=True, align='C')
    pdf.cell(0, 15, txt=f"Title: {title}", ln=True, align='C')
    pdf.cell(0, 15, txt=f"Project: {proj_id}", ln=True, align='C')
    pdf.cell(0, 15, txt=f"Type: {doc_type}", ln=True, align='C')
    pdf.cell(0, 15, txt=f"Status: {status}", ln=True, align='C')
    pdf.cell(0, 15, txt=f"Prepared for: Sales Pitch Presentation", ln=True, align='C')
    
    pdf.output(filepath)
    print(f"Created: {filepath}")

# Update the FilePath column to match the actual relative path
cursor.execute("""
    UPDATE t_EngineeringDocument 
    SET FilePath = 'static/uploads/' || FileName 
    WHERE FileName IS NOT NULL
""")
conn.commit()
conn.close()
print("All PDFs generated and database updated.")