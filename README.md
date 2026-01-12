# SkillSlide

SkillSlide is an interactive learning platform designed to make studying engaging, structured, and adaptive.

Users can upload lecture notes, slides, or PDFs, and SkillSlide automatically breaks them into topics. Each topic comes with explanations at multiple levels (from simple school-level to advanced technical), quizzes to test knowledge, and visual aids like graphs and diagrams.

---

## Key Features

- Multi-level explanations for each topic: school / student / technical
- Automatic extraction of topics from uploaded files
- Support for visuals and diagrams
- Interactive quizzes to reinforce learning
- Progress tracking and goal setting for exams or milestones
- Modular design to support multiple subjects and languages

SkillSlide combines the simplicity of a note-taking system, the interactivity of educational apps, and the power of AI to help users learn smarter and faster.

---

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/SkillSlide.git
cd SkillSlide/backend

# Create virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate  # Linux / macOS
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Run the backend
uvicorn main:app --reload

```


The API will run at http://127.0.0.1:8000.

API Example (MVP)
Subjects
POST /subjects/
Content-Type: application/json

{
  "name": "Mathematics for AI",
  "description": "Limits, sequences, linear algebra",
  "language": "en"
}

GET /subjects/

Upload File to Subject
POST /subjects/{subject_id}/files/
Content-Type: multipart/form-data
File: lecture.pdf


The backend will extract text from the PDF and generate topics (stub implementation for MVP).

Topics & Lessons
GET /subjects/{subject_id}/topics/


Each topic includes lessons with multiple explanation levels (school / student / technical) and placeholders for visuals.

Roadmap / Future Features

AI-powered explanation generation via OpenAI API

Automatic visualization / graph generation for lessons

Multi-language support (top 10 global languages)

Exam mode: focus on key topics and track progress

User authentication and cloud storage for uploaded files

Gamification: points, levels, achievements

Mobile app deployment (iOS / Android)
