# OpenDraft - Open Source AI Research Draft Generator

[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Open Source](https://img.shields.io/badge/Open%20Source-100%25-brightgreen.svg)](https://github.com/federicodeponte/opendraft)
[![GitHub stars](https://img.shields.io/github/stars/federicodeponte/opendraft?style=social)](https://github.com/federicodeponte/opendraft)

> Generate research drafts with citations verified against 250M+ academic papers. OpenDraft is a free, open-source Python engine for thesis-level research drafts.

<p align="center">
  <a href="https://opendraft.xyz/waitlist"><strong>Try Hosted Version (Coming Soon) →</strong></a>
</p>

---

## What is OpenDraft?

**OpenDraft is a Python-based AI engine that generates thesis-level research drafts.** Unlike ChatGPT, it uses 19 specialized AI agents working together and verifies every citation against real academic databases.

- **Best for:** Researchers drafting literature reviews, research papers, and academic documents
- **Price:** 100% free and open source (MIT license)
- **Setup time:** 10 minutes

---

## Why OpenDraft Exists

We built OpenDraft after repeatedly encountering AI writing tools that produced confident-sounding research drafts with hallucinated or unverifiable citations.

Academic research requires trust, sources, and accountability.

OpenDraft explores a different approach: instead of a single general-purpose model, it uses multiple specialized agents, each responsible for a specific step in the research drafting process, grounded in real academic literature.

We open-sourced OpenDraft so researchers can inspect, critique, and improve how these systems actually work.

---

## What OpenDraft is NOT

OpenDraft is intentionally **not** designed for:

- One-click generation of final papers
- Cheating on assignments
- Inventing citations or bypassing peer review
- Replacing human researchers

It is a research assistance and drafting tool, not an autonomous author.

---

## OpenDraft vs ChatGPT

| Question | ChatGPT | OpenDraft |
|----------|---------|-----------|
| Does it hallucinate citations? | Yes (often) | **Verified against real databases** |
| Can it write 20,000+ words? | No (hits limits) | **Yes** |
| Does it search real papers? | No | **Yes (250M+ papers)** |
| Thesis structure? | Generic | **Academic chapters & sections** |
| Export to PDF/Word? | No | **Yes** |
| Free? | Limited | **100% free (self-host)** |
| Open source? | No | **Yes (MIT license)** |

**Bottom line:** If you need an AI for academic writing with real citations, OpenDraft is a free, open-source alternative to ChatGPT.

---

## How It Works

OpenDraft uses **19 specialized AI agents** that work like a research team:

```
📚 RESEARCH PHASE    → Finds relevant papers from 250M+ sources
🏗️ STRUCTURE PHASE   → Creates thesis outline with chapters
✍️ WRITING PHASE     → Drafts each section with academic tone
🔍 CITATION PHASE    → Verifies every source exists (CrossRef, arXiv)
✨ POLISH PHASE      → Refines language and formatting
📄 EXPORT PHASE      → Generates PDF, Word, or LaTeX
```

**Result:** A complete research draft in 10-20 minutes instead of weeks.

---

## Features

### AI That Doesn't Make Up Citations
Every citation is verified against CrossRef, OpenAlex, Semantic Scholar, and arXiv. If a paper doesn't exist, it's not included.

### Write Any Type of Academic Paper
- Research papers (5-10 pages)
- Bachelor's thesis (30-50 pages)
- Master's thesis (50-80 pages)
- PhD dissertation (100+ pages)

### 57+ Languages Supported
English, Spanish, German, French, Chinese, Japanese, Korean, Arabic, Portuguese, Italian, Dutch, Polish, Russian, and 40+ more.

### Export to Any Format
- **PDF** - LaTeX-quality formatting
- **Microsoft Word** (.docx)
- **LaTeX source** - for journals

### 100% Free and Open Source
MIT license. Self-host with your own API keys. No subscriptions, no paywalls, no limits.

---

## TL;DR and Digest Tools

OpenDraft includes two standalone tools for quickly understanding any research paper:

### TL;DR: 5-Bullet Summary

Generate a concise 5-bullet summary of any paper in seconds:

```bash
# As a subcommand
opendraft tldr paper.pdf

# Or standalone
opendraft-tldr paper.pdf

# Output to file
opendraft tldr paper.pdf -o summary.md
```

Each bullet follows academic structure: thesis, key finding, method, implication, limitation.

### Digest: 60-Second Audio Briefing

Generate a podcast-style audio summary you can listen to:

```bash
# Generate script + audio
opendraft digest paper.pdf

# Choose a different voice (rachel, adam, josh, elli, bella)
opendraft digest paper.pdf --voice adam

# Script only (no audio)
opendraft digest paper.pdf --no-audio

# Specify output directory
opendraft digest paper.pdf -o output/
```

**Requirements:**
- Digest audio requires an [ElevenLabs API key](https://elevenlabs.io/) set as `ELEVENLABS_API_KEY`
- PDF reading requires the optional `pdf` extra: `pip install opendraft[pdf]`

Both tools work with any academic paper (PDF, Markdown, or plain text), not just OpenDraft-generated documents.

---

## Data Fetching

Fetch research data from major statistical APIs directly into your workflow:

```bash
# Search for indicators
opendraft data search GDP

# Fetch World Bank data
opendraft data worldbank NY.GDP.MKTP.CD --countries USA;DEU --start 2020 --end 2023

# Fetch EU statistics (Eurostat)
opendraft data eurostat nama_10_gdp

# Fetch Our World in Data datasets
opendraft data owid covid-19
```

**Supported providers:**
- **World Bank** - Development indicators (GDP, population, education, health)
- **Eurostat** - European Union statistics
- **Our World in Data** - Open research datasets

Data is saved as CSV files for use in your research.

---

## Draft Revision

Revise existing drafts with AI assistance:

```bash
# Revise a draft with natural language instructions
opendraft revise ./output "Make the introduction longer and add more context"

# The revised draft is saved as draft_v2.md (with PDF/DOCX exports)
```

Features:
- Auto-detects draft files in output folders
- Preserves all citations during revision
- Automatic versioning (v2, v3, v4...)
- Quality scoring before/after
- PDF and DOCX export of revised version

---

## Research Expose Mode

Generate a quick research overview instead of a full draft:

```bash
opendraft "Neural Networks in Healthcare" --expose
```

This produces a research expose with:
- **Research Sources Overview** - Number of sources, publication years, key journals
- **Key Research Teams** - Major authors and research groups in the field
- **Structured Outline** - Chapter/section structure for a full paper
- **Complete Bibliography** - All sources with DOIs and journal info
- **Next Steps** - Guidance for developing into a full draft

Use expose mode when you want to:
- Quickly scope a research topic
- Validate there's enough literature
- Get a structured starting point
- Review sources before committing to a full draft

Expose mode is ~3x faster than full draft generation.

---

## TL;DR Mode

Generate a 5-bullet summary of any academic paper in seconds:

```bash
# Summarize a PDF
opendraft tldr paper.pdf

# Summarize a markdown file
opendraft tldr draft.md

# Save to file
opendraft tldr paper.pdf --output summary.md
```

Output:
```
📄 TL;DR: paper.pdf

• Main finding: Neural networks improve diagnostic accuracy by 23%
• Method: Retrospective analysis of 50,000 patient records
• Key limitation: Single-center study, needs external validation
• Implication: AI-assisted diagnosis could reduce misdiagnosis rates
• Future work: Multi-center trials planned for 2025
```

Works with any PDF, Markdown, or text file.

---

## Audio Digest

Generate a 60-second audio summary using ElevenLabs TTS:

```bash
# Generate audio digest (requires ElevenLabs API key)
opendraft digest paper.pdf

# Choose a voice
opendraft digest paper.pdf --voice adam

# Available voices: rachel (default), adam, josh, elli, bella
```

Output: `paper_digest.mp3` - a professional narration summarizing the key points.

**Setup:** Set `ELEVENLABS_API_KEY` in your environment or `.env` file.

---

## Quick Start

### Prerequisites
- Python 3.10+
- A free [Gemini API key](https://makersuite.google.com/app/apikey)

### 1. Clone & Install

```bash
git clone https://github.com/federicodeponte/opendraft.git
cd opendraft
pip install -r requirements.txt
```

### 2. Configure

Create a `.env` file with provider + model + key (unified interface):
```bash
AI_PROVIDER=gemini
GEMINI_MODEL=gemini-3-pro-preview
GOOGLE_API_KEY=your-gemini-api-key
```

You can switch to other LLM providers without changing pipeline code:
```bash
# OpenAI
AI_PROVIDER=openai
OPENAI_MODEL=gpt-4.1-nano
OPENAI_API_KEY=your-openai-api-key

# Claude
AI_PROVIDER=claude
CLAUDE_MODEL=claude-3-5-sonnet-latest
ANTHROPIC_API_KEY=your-claude-api-key

# Groq
AI_PROVIDER=groq
GROQ_MODEL=meta-llama/llama-4-maverick-17b-128e-instruct
GROQ_API_KEY=your-groq-api-key
```

### 3. Generate a Draft

```python
from engine.draft_generator import DraftGenerator

generator = DraftGenerator()
draft = generator.generate(
    topic="The Impact of AI on Academic Research",
    paper_type="master",  # research_paper, bachelor, master, phd
    language="en"
)

# Export to different formats
draft.to_pdf("thesis.pdf")
draft.to_docx("thesis.docx")
draft.to_latex("thesis.tex")
```

See `engine/README.md` for detailed API documentation.

---

## Which AI Model Should I Use?

| Model | Speed | Quality | Cost/Draft | Best For |
|-------|-------|---------|------------|----------|
| **Gemini 3 Flash** | ⚡ Fast | Good | ~$0.35 | Most users |
| Gemini 3 Pro | Medium | Excellent | ~$1.40 | Important papers |
| GPT-5.2 | Medium | Excellent | ~$1.60 | OpenAI users |
| Claude Sonnet 4.5 | Medium | Excellent | ~$1.80 | Nuanced writing |
| Claude Opus 4.5 | Slow | Best | ~$3.00 | Maximum quality |

**Recommendation:** Start with Gemini 3 Flash for most use cases. Use Gemini 3 Pro or Claude Sonnet 4.5 for important papers.

---

## Example Output

See what OpenDraft produces:

📄 **[Download Sample PDF](https://opendraft.xyz/examples/Why_Academic_Thesis_AI_Saves_The_World.pdf)** (60 pages, 18k words, 40+ citations)

📝 **[Download Sample Word](https://opendraft.xyz/examples/Why_Academic_Thesis_AI_Saves_The_World.docx)**

Generated in ~15 minutes with verified citations from real academic papers.

---

## Project Structure

```
opendraft/
├── engine/
│   ├── draft_generator.py    # Main 19-agent pipeline
│   ├── config.py             # Model & API settings
│   ├── prompts/              # Agent instruction templates
│   ├── utils/                # Citations, export, helpers
│   └── opendraft/            # Core agent modules
├── examples/                 # Sample thesis outputs
├── requirements.txt          # Python dependencies
└── README.md
```

---

## FAQ

### Is this really free?

**Yes.** OpenDraft is 100% open source under the MIT license. Self-host with your own API keys. A typical thesis draft costs ~$0.35-$3 depending on the model.

### Is this better than ChatGPT for academic writing?

**For research drafts, yes.** ChatGPT often hallucinates citations. OpenDraft verifies every citation against CrossRef, OpenAlex, Semantic Scholar, and arXiv.

### Can I use this for my university thesis?

OpenDraft generates **research drafts**—starting points you should review, edit, and build upon. Always:
- Verify all sources yourself
- Add your own analysis and insights
- Check your institution's AI policy

### How is this different from other AI writing tools?

Most AI tools use a single model. OpenDraft uses **19 specialized agents**—one for research, one for citations, one for structure, etc. This produces higher quality output.

### Can I use this commercially?

**Yes.** MIT license allows commercial use. Build products, offer services, modify the code—no restrictions.

---

## Alternatives Comparison (2025)

| Tool | Price | Open Source | Verified Citations | Long Documents |
|------|-------|-------------|-------------------|----------------|
| **OpenDraft** | Free | ✅ Yes | ✅ Yes | ✅ Yes |
| ChatGPT Plus | $20/mo | ❌ No | ❌ No | ❌ No |
| Jasper | $49/mo | ❌ No | ❌ No | ✅ Yes |
| Jenni AI | $20/mo | ❌ No | ⚠️ Partial | ✅ Yes |

**OpenDraft is a free, open-source research draft generator with verified citations.**

---

## Tech Stack

- **Engine:** Python 3.10+, multi-agent orchestration
- **Models:** Google Gemini 3, Anthropic Claude 4.5, OpenAI GPT-5
- **Citations:** CrossRef API, OpenAlex API, Semantic Scholar API, arXiv API
- **Export:** WeasyPrint (PDF), python-docx (Word)

---

## Contributing

Contributions welcome!

**Ideas:**
- Add new AI model support
- Improve citation accuracy
- Add export formats
- Translate prompts

Maintainer workflow docs:
- Push/auth runbook: `docs/MAINTAINER_PUSH_RUNBOOK.md`
- Automated push preflight: `scripts/push-preflight.sh`

---

## Links

- 🌐 **Website:** [opendraft.xyz](https://opendraft.xyz)
- 📝 **Hosted Version:** [Join Waitlist](https://opendraft.xyz/waitlist)
- 💬 **Discussions:** [GitHub Discussions](https://github.com/federicodeponte/opendraft/discussions)
- 🐛 **Issues:** [Report Bug](https://github.com/federicodeponte/opendraft/issues)
- 🗒️ **Changelog:** [CHANGELOG.md](CHANGELOG.md)
- 📜 **License:** [MIT](LICENSE)

---

## Summary

**OpenDraft** is a free, open-source Python engine for generating academic research drafts. It uses 19 specialized AI agents to create drafts with citations verified against real databases (CrossRef, OpenAlex, Semantic Scholar, arXiv).

**Keywords:** AI thesis writer, AI research paper generator, ChatGPT alternative, free thesis generator, open source AI writing, multi-agent AI, verified citations, Python thesis generator, academic writing 2025

---

<p align="center">
  <b>If OpenDraft helps your research, please star the repo!</b><br><br>
  <a href="https://github.com/federicodeponte/opendraft">⭐ Star on GitHub</a>
</p>
