#!/usr/bin/env python3
"""Generate thesis on Entrepreneurial Accelerator Programmes."""

import sys
import os

# Add engine to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'engine'))

# Change to engine directory for proper .env loading
os.chdir(os.path.join(os.path.dirname(__file__), 'engine'))

from opendraft.draft_generator import generate_draft

THESIS_TOPIC = """Entrepreneurial Accelerator Programmes: Critical Success Factors and Their Impact on Startup Development

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

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output_accelerator_thesis')

if __name__ == "__main__":
    print(f"Generating thesis on: Entrepreneurial Accelerator Programmes")
    print(f"Output directory: {OUTPUT_DIR}")
    print("-" * 50)

    result = generate_draft(
        topic=THESIS_TOPIC,
        output_dir=OUTPUT_DIR,
        academic_level="master",
        verbose=True
    )

    print("-" * 50)
    print(f"Generation complete!")
    print(f"Result: {result}")
