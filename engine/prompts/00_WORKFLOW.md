# OpenDraft - Complete Workflow Guide

**Welcome!** This guide will walk you through writing a complete academic paper using AI agents.

**Estimated Time:** 1-2 weeks (depending on paper complexity)

---

## 🎯 Quick Overview

You'll progress through 5 phases:
1. **Research** (1-3 days) - Find and analyze papers
2. **Structure** (1 day) - Design paper outline
3. **Compose** (2-5 days) - Write all sections
4. **Validate** (1-2 days) - Critical review and verification
5. **Refine** (1-2 days) - Polish and finalize

**Your inputs at each step:** Research topic, paper type, target journal
**Your outputs:** Publication-ready academic paper

**Chinese / bilingual support:** You may start with a Chinese topic, a Chinese paper title, a mixed Chinese-English query, or a Chinese thesis-writing goal. The upgraded prompt chain is designed to preserve bilingual titles, normalize terminology, and maintain Chinese academic writing quality across all phases.

---

## 📋 Before You Start

### ✅ Prerequisites Checklist

- [ ] MCP servers installed (run `./mcp_servers/install_all.sh`)
- [ ] `.env` file configured with API keys
- [ ] IDE restarted (Claude Code or Cursor)
- [ ] Created project folder structure:
  ```
  your-paper/
  ├── research/
  │   ├── sources.md
  │   ├── summaries.md
  │   └── gaps.md
  ├── sections/
  │   ├── 01_introduction.md
  │   ├── 02_literature.md
  │   ├── 03_methodology.md
  │   ├── 04_results.md
  │   ├── 05_discussion.md
  │   └── 06_conclusion.md
  ├── outline.md
  ├── outline_formatted.md
  └── final_draft.md
  ```

### 📝 Define Your Paper

**Research Topic:** ______________________________________

**Paper Type:**
- [ ] Literature Review
- [ ] Empirical Study (IMRaD)
- [ ] Theoretical Paper
- [ ] Mixed Methods

**Target Venue:** ______________________________________

**Word Limit:** ________ words

**Citation Style:** [ ] APA  [ ] MLA  [ ] Chicago  [ ] IEEE

**Deadline:** ________

**Primary Language Mode:**
- [ ] Chinese-first
- [ ] English-first
- [ ] Bilingual support

---

## Phase 1: RESEARCH (Days 1-3)

**Goal:** Gather and analyze 20-50 relevant papers

### Step 1: Find Sources (Scout Agent)
⏱️ **Time:** 30-60 min

**Instructions:**
1. Open `prompts/01_research/scout.md`
2. Read the prompt to understand what Scout Agent does
3. Open your IDE chat (Claude Code / Cursor)
4. Paste the prompt
5. Add your research topic:
   ```
   Topic: "AI applications in climate modeling"

   Requirements:
   - Focus on deep learning methods
   - Papers from 2020-2024
   - Include review papers
   - Prioritize high-impact journals
   ```
   Or, for Chinese workflows:
   ```
   主题："大模型在医疗问答中的应用研究"

   要求：
   - 支持中文标题论文检索
   - 同时覆盖国际英文文献
   - 输出保留中英文题名映射
   - 优先近五年高质量研究
   ```
6. Submit and wait for agent to search using MCP tools
7. Agent will return 20-50 papers with metadata

**Save output to:** `research/sources.md`

**✅ Checklist:**
- [ ] Scout Agent returned 20+ papers
- [ ] Papers are relevant to topic
- [ ] Mix of recent (2022-2024) and foundational papers
- [ ] Saved to `research/sources.md`

---

### Step 2: Summarize Papers (Scribe Agent)
⏱️ **Time:** 1-2 hours

**Instructions:**
1. Open `prompts/01_research/scribe.md`
2. Open IDE chat
3. Paste the prompt
4. **Attach** `research/sources.md` to the chat
5. Submit

**Agent will:**
- Deep-read each paper (using arXiv MCP for full PDFs when available)
- Extract key findings, methods, limitations
- Identify connections between papers
- Analyze research trajectory
- When Chinese-led or bilingual, preserve original titles, English titles, and terminology alignment

**Save output to:** `research/summaries.md`

**✅ Checklist:**
- [ ] All papers have summaries
- [ ] Key findings extracted
- [ ] Cross-paper connections identified
- [ ] Saved to `research/summaries.md`

---

### Step 3: Identify Gaps (Signal Agent)
⏱️ **Time:** 1-2 hours

**Instructions:**
1. Open `prompts/01_research/signal.md`
2. Open IDE chat
3. Paste the prompt
4. **Attach** `research/summaries.md`
5. Submit

**Agent will:**
- Identify research gaps
- Spot emerging trends
- Find contradictions in literature
- Suggest novel research angles

**Save output to:** `research/gaps.md`

**✅ Checklist:**
- [ ] 3-7 major gaps identified
- [ ] Novel research angles suggested
- [ ] Opportunities ranked by feasibility
- [ ] Saved to `research/gaps.md`

**🎉 MILESTONE:** Research phase complete! You now have a comprehensive literature analysis.

---

## Phase 2: STRUCTURE (Day 4)

**Goal:** Design logical paper structure and argument flow

### Step 3.5: Extract Citations (Citation Manager)
⏱️ **Time:** 5-10 min (automated)

**Instructions:**
1. Open `prompts/02_structure/citation_manager.md`
2. This step extracts all citations from your research materials into a structured database
3. Open IDE chat
4. Paste the prompt
5. **Attach** `research/sources.md` AND `research/summaries.md`
6. Submit

**Agent will:**
- Extract all citations from research materials using LLM
- Create structured citation database (JSON format)
- Assign citation IDs (cite_001, cite_002, etc.)
- Validate all required metadata (authors, year, title, etc.)
- Store in `research/citations.json`

**Save output to:** `research/citations.json`

**✅ Checklist:**
- [ ] Citation database created successfully
- [ ] All citations have complete metadata
- [ ] Citation IDs assigned sequentially
- [ ] Saved to `research/citations.json`

**📝 Important:** These citation IDs will be used in ALL subsequent sections. Writers will reference `{cite_001}` instead of inline citations like "(Smith, 2023)". This enables 100% deterministic citation formatting in the final step.

---

### Step 4: Create Outline (Architect Agent)
⏱️ **Time:** 1-2 hours

**Instructions:**
1. Open `prompts/02_structure/architect.md`
2. Open IDE chat
3. Paste the prompt
4. **Attach** `research/gaps.md`
5. Add paper specifications:
   ```
   Paper Type: Empirical Study (IMRaD format)
   Target Journal: Nature Machine Intelligence
   Research Question: [Your specific question]
   ```
6. Submit

**Agent will:**
- Design complete paper structure
- Map argument flow (intro → methods → results → discussion)
- Plan evidence placement
- Suggest figures/tables

**Save output to:** `outline.md`

**✅ Checklist:**
- [ ] All required sections present
- [ ] Logical argument flow
- [ ] Evidence placement planned
- [ ] Saved to `outline.md`

---

### Step 5: Apply Format (Formatter Agent)
⏱️ **Time:** 30 min

**Instructions:**
1. Open `prompts/02_structure/formatter.md`
2. Open IDE chat
3. Paste the prompt
4. **Attach** `outline.md`
5. Specify:
   ```
   Target Journal: Nature Machine Intelligence
   Citation Style: APA 7th edition
   Word Limit: 8000 words
   ```
6. Submit

**Agent will:**
- Apply journal-specific formatting
- Add section numbering, heading styles
- Specify citation format
- Include submission requirements

**Save output to:** `outline_formatted.md`

**✅ Checklist:**
- [ ] Format matches journal guidelines
- [ ] All formatting details specified
- [ ] Word count targets per section
- [ ] Saved to `outline_formatted.md`

**🎉 MILESTONE:** Structure phase complete! You have a publication-ready outline.

---

## Phase 3: COMPOSE (Days 5-9)

**Goal:** Write all sections with proper citations and flow

### Writing Strategy

**Recommended order:**
1. Methods (easiest - you know what you did)
2. Results (data-driven, objective)
3. Introduction (now you know what you're introducing)
4. Literature Review (you know what's relevant)
5. Discussion (you know what to discuss)
6. Conclusion (recap what you wrote)
7. Abstract (last - summarizes everything)

---

### Step 6: Write Sections (Crafter Agent)
⏱️ **Time:** 3-6 hours per section

**For EACH section:**

1. Open `prompts/03_compose/crafter.md`
2. Open IDE chat
3. Paste the prompt
4. **Attach** `outline_formatted.md` AND `research/summaries.md`
5. Specify which section:
   ```
   Section to write: INTRODUCTION

   Key points to cover (from outline):
   - Hook about climate change urgency
   - Current state of AI in climate modeling
   - Gap: lack of hybrid physical-AI models
   - Our contribution: novel hybrid approach

   Target length: 800-1000 words
   ```
6. Submit

**Save each section to:**
- `sections/01_introduction.md`
- `sections/02_literature.md`
- `sections/03_methodology.md`
- `sections/04_results.md`
- `sections/05_discussion.md`
- `sections/06_conclusion.md`

**✅ Per-Section Checklist:**
- [ ] Matches outline structure
- [ ] Proper citations throughout
- [ ] Clear, academic prose
- [ ] Target word count achieved
- [ ] Saved to appropriate file

---

### Step 7: Check Consistency (Thread Agent)
⏱️ **Time:** 1 hour

**After ALL sections written:**

1. Open `prompts/03_compose/thread.md`
2. Open IDE chat
3. Paste the prompt
4. **Attach ALL section files:**
   - sections/01_introduction.md
   - sections/02_literature.md
   - sections/03_methodology.md
   - sections/04_results.md
   - sections/05_discussion.md
   - sections/06_conclusion.md
5. Submit

**Agent will:**
- Check narrative consistency across sections
- Find contradictions or gaps
- Verify cross-references
- Suggest transition improvements

**Action:** Fix identified issues in section files

**✅ Checklist:**
- [ ] No contradictions between sections
- [ ] Cross-references valid
- [ ] Smooth transitions
- [ ] Issues fixed

---

### Step 8: Unify Voice (Narrator Agent)
⏱️ **Time:** 1 hour

**Instructions:**
1. Open `prompts/03_compose/narrator.md`
2. Open IDE chat
3. Paste the prompt
4. **Attach ALL section files**
5. Specify target journal formality level
6. Submit

**Agent will:**
- Ensure consistent tone throughout
- Check person/tense usage
- Standardize vocabulary
- Fix voice inconsistencies

**Action:** Apply suggested voice adjustments

**✅ Checklist:**
- [ ] Consistent tone across sections
- [ ] Proper tense usage
- [ ] Unified vocabulary
- [ ] Adjustments applied

**🎉 MILESTONE:** Composition phase complete! You have a full draft.

---

## Phase 4: VALIDATE (Days 10-11)

**Goal:** Critical review, fact-checking, and quality assurance

### Step 9: Critical Review (Skeptic Agent)
⏱️ **Time:** 1-2 hours

**Instructions:**
1. Assemble complete draft:
   ```bash
   cat sections/*.md > full_draft.md
   ```
2. Open `prompts/04_validate/skeptic.md`
3. Open IDE chat
4. Paste the prompt
5. **Attach** `full_draft.md`
6. Submit

**Agent will:**
- Challenge weak arguments
- Identify overclaims
- Find logical flaws
- Suggest strengthening

**Action:** Address all critical and moderate issues

**✅ Checklist:**
- [ ] All critical issues resolved
- [ ] Arguments strengthened
- [ ] Claims match evidence
- [ ] Limitations acknowledged

---

### Step 10: Verify Citations (Verifier Agent)
⏱️ **Time:** 1 hour

**Instructions:**
1. Open `prompts/04_validate/verifier.md`
2. Open IDE chat
3. Paste the prompt
4. **Attach** `full_draft.md`
5. Submit

**Agent will** (using Semantic Scholar MCP):
- Verify citation accuracy
- Check claims match sources
- Validate DOIs and metadata
- Ensure reference list complete

**Action:** Fix all citation errors

**✅ Checklist:**
- [ ] All citations verified
- [ ] Claims match sources
- [ ] Reference list complete
- [ ] Format consistent

---

### Step 11: Reviewer Simulation (Referee Agent)
⏱️ **Time:** 1-2 hours

**Instructions:**
1. Open `prompts/04_validate/referee.md`
2. Open IDE chat
3. Paste the prompt
4. **Attach** `full_draft.md`
5. Specify target journal
6. Submit

**Agent will:**
- Score on review rubric (novelty, significance, rigor, clarity)
- Predict reviewer feedback
- Identify acceptance likelihood
- Suggest pre-emptive improvements

**Action:** Address predicted reviewer concerns

**✅ Checklist:**
- [ ] Overall score > 3/5
- [ ] Major concerns addressed
- [ ] Paper submission-ready
- [ ] Improvements made

**🎉 MILESTONE:** Validation complete! Paper is rigorous and defensible.

---

## Phase 5: REFINE (Days 12-13)

**Goal:** Polish style, ensure human-like writing, finalize

### Step 12: Match Your Voice (Voice Agent) - OPTIONAL
⏱️ **Time:** 1 hour

**Instructions:**
1. Gather 2-3 samples of your own academic writing
2. Open `prompts/05_refine/voice.md`
3. Open IDE chat
4. Paste the prompt
5. **Attach:**
   - `full_draft.md`
   - Your writing samples
6. Submit

**Agent will:**
- Analyze your natural writing style
- Adjust paper to match your voice
- Maintain academic standards

**✅ Checklist:**
- [ ] Style feels natural
- [ ] Matches your voice
- [ ] Still professional

---

### Step 13: Increase Naturalness (Entropy Agent)
⏱️ **Time:** 1-2 hours

**Instructions:**
1. Open `prompts/05_refine/entropy.md`
2. Open IDE chat
3. Paste the prompt
4. **Attach** `full_draft.md`
5. Submit

**Agent will:**
- Vary sentence structure
- Diversify vocabulary
- Add natural rhythm
- Reduce AI-detectable patterns

⚠️ **Ethical Note:** This improves writing quality, NOT disguises authorship. Use responsibly.

**Optional AI Detection Test:**
```bash
python utils/ai_detection.py full_draft.md
```

**Target:** < 20% AI detection score

**✅ Checklist:**
- [ ] Sentence length varied
- [ ] Vocabulary diverse
- [ ] Natural flow
- [ ] AI score acceptable (if tested)

---

### Step 14: Final Polish (Polish Agent)
⏱️ **Time:** 30-60 min

**Instructions:**
1. Open `prompts/05_refine/polish.md`
2. Open IDE chat
3. Paste the prompt
4. **Attach** current draft
5. Submit

**Agent will:**
- Fix grammar and spelling
- Check punctuation
- Improve readability
- Final formatting check

**This is the LAST editing step!**

**✅ Checklist:**
- [ ] No grammar errors
- [ ] Spelling consistent
- [ ] Punctuation correct
- [ ] Reads smoothly

**🎉 MILESTONE:** Refinement complete! Paper is polished and ready for enhancement.

---

## PHASE 5.5: CITATION COMPILATION (Agent #14)

**Goal:** Replace citation IDs with formatted citations and generate reference list

### Step 14.5: Compile Citations (Citation Compiler Agent) 🆕
⏱️ **Time:** Instant (deterministic)

**Instructions:**
1. This step is AUTOMATIC if you used citation IDs from Step 3.5
2. The Citation Compiler performs dictionary lookup (O(1) complexity)
3. **No LLM needed** - This is deterministic compilation

**What happens:**
```python
# Your draft has citation IDs:
Recent studies {cite_001} show effectiveness...

# Citation Compiler replaces them:
Recent studies (Smith et al., 2023) show effectiveness...

# Reference list is auto-generated from cited IDs
```

**Agent will:**
- Scan for all `{cite_XXX}` patterns in draft
- Look up each ID in citation database (from Step 3.5)
- Format according to citation style (APA 7th, IEEE, MLA)
- Replace IDs with formatted citations
- Generate reference list with ONLY cited sources
- Sort references alphabetically (APA style)

**Save output to:** `draft_with_citations.md`

**✅ Checklist:**
- [ ] All `{cite_XXX}` IDs replaced
- [ ] Citations formatted correctly (APA 7th)
- [ ] Reference list auto-generated
- [ ] No missing citations
- [ ] 100% compilation success

**📊 Expected Results:**
- **Compilation time:** < 1 second (dictionary lookup)
- **Success rate:** 100% (deterministic)
- **Missing citations:** 0

**📝 Note:** This replaces the old Agent #14 (Citation Verifier) which searched for `[VERIFY]` tags. The new architecture prevents citation placeholders at the source, ensuring zero manual verification needed.

---

## PHASE 6: ENHANCE

**Goal:** Transform complete draft into publication-ready showcase with professional elements

### Step 15: Professional Enhancement (Enhancer Agent) 🆕
⏱️ **Time:** 3-5 min (automatic)

**What it does:**
Automatically adds professional elements that make your draft publication-ready:
- YAML metadata frontmatter
- Enhanced 4-paragraph abstract with keywords
- 5 comprehensive appendices (domain-specific)
- Limitations section (4-5 subsections)
- Future Research section (5-7 directions)
- 3-5 professional tables
- 1-2 ASCII figures/diagrams
- Expanded case studies with quantitative data

**Instructions:**
1. Run the test script with enhancement enabled (now automatic):
   ```bash
   python tests/scripts/test_opensource_draft.py
   ```

2. The enhancer runs automatically after Agent #13 (Entropy)

3. Output will be saved as `15_enhanced_final.md`

**Expected Output:**
- **Before Enhancement:** ~8,000-10,000 words, 29-39 pages
- **After Enhancement:** ~14,000-16,000 words, 60-70 pages
- **Added:** YAML metadata + 5 appendices + Limitations + Future Research + visual elements

**Agent will:**
- Analyze draft domain and content
- Generate appropriate appendices (mathematical frameworks, case studies, resources, glossary)
- Add Limitations section with methodological, scope, and theoretical constraints
- Add Future Research section with 5-7 specific research directions
- Create comparative tables and ASCII diagrams
- Expand case studies with detailed quantitative metrics
- Add YAML frontmatter with accurate metadata

**✅ Checklist:**
- [ ] YAML frontmatter added with accurate stats
- [ ] Abstract enhanced into 4 labeled paragraphs
- [ ] 5 comprehensive appendices present
- [ ] Limitations section added (~800 words)
- [ ] Future Research section added (~800 words)
- [ ] 3-5 tables added in Analysis/Discussion sections
- [ ] 1-2 figures added in Methodology/Theory sections
- [ ] Word count increased by ~6,000-7,000 words

**⚠️ Important:**
- All original citations are preserved
- No content is removed, only added
- Domain-specific appendices are automatically generated based on draft content
- Enhancement maintains academic integrity

**🎉 MILESTONE:** Enhancement complete! Draft is now publication-ready showcase quality.

---

## Final Steps: EXPORT & SUBMIT

### Step 16: Generate Final Document
⏱️ **Time:** 15 min (automatic)

**Assemble final paper:**
```bash
cat sections/01_introduction.md \
    sections/02_literature.md \
    sections/03_methodology.md \
    sections/04_results.md \
    sections/05_discussion.md \
    sections/06_conclusion.md \
    > final_draft.md
```

**Export to submission format:**
```bash
# PDF (publication quality)
python utils/export.py --format pdf --output final_draft.pdf

# Word document (for submission portals)
python utils/export.py --format docx --output final_draft.docx

# LaTeX (if journal requires)
python utils/export.py --format latex --output final_draft.tex
```

---

### Step 16: Final Checklist

**Before submission:**
- [ ] Abstract within word limit (150-250 words)
- [ ] Introduction clearly states research question
- [ ] Methods enable replication
- [ ] Results present all data objectively
- [ ] Discussion addresses all findings
- [ ] Conclusion emphasizes contribution
- [ ] All figures/tables referenced in text
- [ ] All claims have citations
- [ ] References formatted correctly
- [ ] Author information complete
- [ ] Acknowledgments included (if applicable)
- [ ] Ethics statement (if required)
- [ ] Data availability statement (if required)
- [ ] No placeholder text ([TODO], [FIXME], etc.)
- [ ] Read ETHICS.md and comply with institutional policies
- [ ] Advisor/supervisor approval (if applicable)

---

### Step 17: Submit!

**Submission materials:**
- [ ] Main manuscript (PDF or Word)
- [ ] Cover letter (introduce your work)
- [ ] Highlights (3-5 bullet points)
- [ ] Graphical abstract (if required)
- [ ] Supplementary materials (if any)
- [ ] Author agreement forms

**Track your submission:**
- Submission date: _______
- Manuscript ID: _______
- Expected decision: _______ (usually 6-8 weeks)

---

## 🎉 CONGRATULATIONS!

You've completed an academic paper using AI assistance!

**What you accomplished:**
- ✅ Comprehensive literature review (20-50 papers)
- ✅ Novel research contribution identified
- ✅ Complete paper written (6000-8000 words)
- ✅ Rigorous validation and fact-checking
- ✅ Publication-ready manuscript

**Time saved:** ~50-70% compared to traditional process
**Quality:** Peer-review ready

---

## 📊 Progress Tracker

Use this to track your progress:

| Phase | Step | Agent | Status | Date |
|-------|------|-------|--------|------|
| **RESEARCH** | 1 | Scout | ⬜ | __ |
| | 2 | Scribe | ⬜ | __ |
| | 3 | Signal | ⬜ | __ |
| **STRUCTURE** | 4 | Architect | ⬜ | __ |
| | 5 | Formatter | ⬜ | __ |
| **COMPOSE** | 6 | Crafter (×6 sections) | ⬜ | __ |
| | 7 | Thread | ⬜ | __ |
| | 8 | Narrator | ⬜ | __ |
| **VALIDATE** | 9 | Skeptic | ⬜ | __ |
| | 10 | Verifier | ⬜ | __ |
| | 11 | Referee | ⬜ | __ |
| **REFINE** | 12 | Voice (optional) | ⬜ | __ |
| | 13 | Entropy | ⬜ | __ |
| | 14 | Polish | ⬜ | __ |
| **ENHANCE** | 15 | Enhancer 🆕 | ⬜ | __ |
| **SUBMIT** | 16 | Export | ⬜ | __ |
| | 17 | Final Check | ⬜ | __ |
| | 18 | Submit | ⬜ | __ |

---

## 🆘 Troubleshooting

**MCP tools not working?**
- Check IDE was restarted after installing MCP servers
- Verify mcp_config.json exists and is valid JSON
- Test individual servers (see `mcp_servers/README.md`)

**Agent responses too generic?**
- Attach more context (research notes, outline)
- Be specific in your instructions
- Iterate with follow-up prompts

**AI detection score too high?**
- Run Entropy Agent again
- Add more of your own writing samples to Voice Agent
- Manually edit some sections for variety

**Running out of time?**
- Prioritize: Research → Structure → Validate → Compose → Refine
- Skip optional steps (Voice Agent)
- Focus on core sections (Intro, Methods, Results, Discussion)

**Need help?**
- Review agent prompt files for detailed instructions
- Check examples/ folder for sample outputs
- See README.md for setup issues
- Consult ETHICS.md for responsible use guidelines

---

## 📚 Next Steps After Submission

**While waiting for reviews:**
- [ ] Start next paper (the system is set up!)
- [ ] Prepare presentation
- [ ] Draft response to anticipated reviewer comments
- [ ] Consider preprint (arXiv, bioRxiv)

**After acceptance:**
- [ ] Share your success!
- [ ] Archive your research files
- [ ] Update CV
- [ ] Plan follow-up research

---

**Good luck with your research!** 🚀

Remember: This tool assists your scholarship, it doesn't replace it. You are the author; the agents are your research assistants.

**Questions?** See README.md or open an issue on GitHub.
