#!/usr/bin/env python3
"""Generate Thesis 6: Entrepreneurial Accelerator Programmes"""
import os
import sys
import zipfile
from pathlib import Path

# Use relative paths for portability
ENGINE_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = ENGINE_DIR.parent
os.chdir(ENGINE_DIR)
sys.path.insert(0, str(ENGINE_DIR))

from draft_generator import generate_draft

OUTPUT_DIR = PROJECT_ROOT / "theses_output" / "thesis_6_accelerator_programmes"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

print("=" * 60)
print("THESIS 6: Entrepreneurial Accelerator Programmes")
print("=" * 60)

topic = """Entrepreneurial Accelerator Programmes: Critical Success Factors and Their Impact on Startup Development

Research Question: To what extent do design characteristics of university-based accelerator programmes shape the development and growth of early-stage student start-ups within European entrepreneurial ecosystems?

Key areas to cover:
1. Literature review on accelerator programme theory and entrepreneurial ecosystems
2. Critical success factors of accelerator programmes (mentorship, funding, networks, curriculum)
3. University-based vs corporate vs independent accelerators - comparative analysis
4. European entrepreneurial ecosystem context (regional differences, policy frameworks)
5. Impact measurement frameworks for startup development and growth
6. Methodology: Qualitative research design with case studies or interviews
7. Analysis of programme design characteristics and their correlation with startup outcomes
8. Recommendations for accelerator programme design and policy implications
"""

print(f"Topic: Entrepreneurial Accelerator Programmes")
print(f"Research Question: Design characteristics of university-based accelerator programmes...")
print("\nStarting generation...")

pdf_path, docx_path = generate_draft(
    topic=topic,
    language="en",
    academic_level="master",
    output_dir=OUTPUT_DIR,
    skip_validation=True,
    verbose=True
)

print("\nGeneration complete!")
print(f"PDF: {pdf_path}")
print(f"DOCX: {docx_path}")

# Create ZIP with both files
zip_path = OUTPUT_DIR / "thesis_6_accelerator_programmes.zip"
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
    if pdf_path.exists():
        zipf.write(pdf_path, pdf_path.name)
    if docx_path.exists():
        zipf.write(docx_path, docx_path.name)

print(f"\nZIP created: {zip_path}")
print("=" * 60)
print("THESIS 6 COMPLETE!")
print("=" * 60)
