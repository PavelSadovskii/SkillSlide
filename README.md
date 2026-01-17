# SkillSlide

SkillSlide is an interactive learning platform designed to make studying engaging, structured, and adaptive.

Users can upload lecture notes, slides, or PDFs, and SkillSlide automatically breaks them into topics. Each topic comes with explanations at multiple levels (from simple school-level to advanced technical), quizzes to test knowledge, and visual aids like graphs and diagrams.

---

## Receipt OCR prototype

This repository also contains a lightweight prototype for receipt parsing. The app lets you upload a receipt file, parses items, and stores data (store name, date, total, line items) in SQLite. You can edit receipt fields, add/remove items, and export data to CSV directly in the UI to keep the database up to date.

### Quick start

```bash
python app.py
```

Then open <http://localhost:5000> and upload a receipt file.

### Notes

- The built-in parser uses simple heuristics and works best with text or CSV receipts.
- Image OCR is not bundled in this offline-friendly version. If you need OCR, integrate Tesseract or a cloud OCR service and adapt `extract_text_from_upload` in `app.py`.

---

## Key Features

- Multi-level explanations for each topic: school / student / technical
- Automatic extraction of topics from uploaded files
- Support for visuals and diagrams
- Interactive quizzes to reinforce learning
- Progress tracking and goal setting for exams or milestones
- Modular design to support multiple subjects and languages

SkillSlide combines the simplicity of a note-taking system, the interactivity of educational apps, and the power of AI to help users learn smarter and faster.
