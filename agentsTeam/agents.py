import os
from dotenv import load_dotenv
from crewai import Agent
from crewai_tools import FileReadTool

load_dotenv()

google_key = os.getenv("GOOGLE_API_KEY", "")
os.environ["GEMINI_API_KEY"] = google_key
os.environ["OPENAI_API_KEY"] = google_key # Routing trick for LiteLLM

# Utility to load Gentle-AI Skills
def load_skill(filename):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, 'skills', filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

tool_leer_codigo = FileReadTool()

class EngineeringFactory:
    @staticmethod
    def architect():
        # MODEL ROUTING: Architect uses a stronger/smarter model for Spec-Driven Development
        return Agent(
            role="Lead Software Architect",
            goal="Design architectural contracts and Technical Specifications (SDD).",
            backstory=load_skill("architect_skill.md"),
            tools=[tool_leer_codigo],
            max_iter=3,
            allow_delegation=False,
            verbose=True,
            llm='gemini/gemini-1.5-pro-latest' # High intelligence for SDD
        )

    @staticmethod
    def engineer():
        # MODEL ROUTING: Executor uses a faster/cheaper model to just output code
        return Agent(
            role="Senior Video Backend Developer",
            goal="Translate the Architect's SDD into production-ready source code.",
            backstory=load_skill("developer_skill.md"),
            tools=[tool_leer_codigo],
            max_iter=3,
            allow_delegation=False,
            verbose=True,
            llm='gemini/gemini-1.5-flash-latest' # Fast/cheap for execution
        )

    @staticmethod
    def qa_tester():
        # MODEL ROUTING: Reviewer uses a fast model for strict validation
        return Agent(
            role="Critique-Bot (QA Reviewer)",
            goal="Ensure the developer's code strictly adheres to the Architect's SDD.",
            backstory=load_skill("reviewer_skill.md"),
            tools=[tool_leer_codigo],
            max_iter=3,
            allow_delegation=True,
            verbose=True,
            llm='gemini/gemini-1.5-flash-latest' # Fast validation
        )
